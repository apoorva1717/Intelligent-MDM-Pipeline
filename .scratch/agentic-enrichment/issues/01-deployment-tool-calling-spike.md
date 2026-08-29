# 01 — Does the deployment actually support the tool contract?

Type: task
Status: ANSWERED — spike run live 2026-08-29, all four questions settled
Blocked by: —

## Question

Can `gpt-5.4` on this Azure deployment run the tool loop the lane depends on? Specifically:

1. Does it accept `tools` + `tool_choice` at all, on the pinned `2024-08-01-preview` API version?
2. Can `response_format={"type":"json_object"}` ride along **with** `tools` in one request?
   This is undocumented by both Azure and OpenAI — neither permitted nor forbidden. The entire
   existing LLM layer (`llm/openai_client.py`) is built on `response_format`, so if the two do not
   compose, the tool layer changes shape before anything else is designed.
3. Is `seed` still accepted on a tool-calling request, and does `system_fingerprint` come back?
   (`seed` is now marked *deprecated* in the current Azure REST reference.)
4. Does `parallel_tool_calls=false` behave? Microsoft documents that structured outputs require it.

Answer with the actual request/response, not with documentation.

## Why this is first

Every design ticket downstream assumes an answer. It is an hour of work and it is a hard blocker
on the tool contract (04), the prompt schema (07), and the cache keying (06).

## Notes

Requires live Azure credentials. If the agent cannot reach the deployment, this is HITL: hand the
human a precise script to run and paste back.

Relevant: `llm/openai_client.py` (`call_openai`, `extract_json`, `seed_supported`),
`.env` (`AZURE_OPENAI_DEPLOYMENT=gpt-5.4`, `AZURE_OPENAI_API_VERSION=2024-08-01-preview`).

---

## Findings

**Status: BLOCKED — HITL. Zero live evidence obtained. No request was sent to Azure.**

The four questions demand an actual request/response, and this environment cannot
produce one: **`.env` contains the `.env.example` placeholder credentials, byte-for-byte.**
Verified without printing any secret:

```
$ diff <(grep ^AZURE_OPENAI_API_KEY=  .env) <(grep ^AZURE_OPENAI_API_KEY=  .env.example)   -> identical
$ diff <(grep ^AZURE_OPENAI_ENDPOINT= .env) <(grep ^AZURE_OPENAI_ENDPOINT= .env.example)   -> identical
```

i.e. `.env` still carries `AZURE_OPENAI_API_KEY=your-azure-key-here` and
`AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/`. `AZURE_OPENAI_API_KEY`
and `AZURE_OPENAI_ENDPOINT` are also unset in the process environment and in both the
Windows **User** and **Machine** env-var scopes, so `load_dotenv()` (which does not
override an existing entry) has nothing real to fall back to. There is no credential
on this machine to probe with, and per this ticket's own note that makes it HITL.

The non-secret half of the config **is** real and was confirmed:

| | |
|---|---|
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-5.4` |
| `AZURE_OPENAI_API_VERSION` | `2024-08-01-preview` (= `DEFAULT_AZURE_OPENAI_API_VERSION` in `llm/openai_client.py`) |
| `AOAI_DEPLOYMENT_DEDUP` / `AOAI_API_VERSION_DEDUP` | `gpt-5.4` / `2025-04-01-preview` |
| openai SDK / httpx / Python | 3.5.0 / 0.28.1 / 3.14.0 |
| `MOCK_EXTERNAL_CALLS` in `.env` | `false` (and the spike pins it to `false` before importing `config`, so a stale shell or a test conftest cannot answer a probe with a mock) |

### The four answers

| # | Question | Answer |
|---|---|---|
| 1 | `tools` + `tool_choice` accepted on `2024-08-01-preview`? | **UNANSWERED** — needs the human run |
| 2 | `response_format={"type":"json_object"}` **with** `tools` in one request? | **UNANSWERED** — needs the human run. *This is the blocker for 04.* |
| 3 | `seed` accepted on a tool-calling request; `system_fingerprint` returned? | **UNANSWERED** for the request half. One half **is** already known from this repo's own measurements (below). |
| 4 | `parallel_tool_calls=false` behaves? | **UNANSWERED** — needs the human run |

Nothing above is inferred from documentation, which is the whole point of the ticket.
A later reader must not mistake an absent answer for a negative one.

### What the codebase already tells us about Q3 (not a substitute for the probe)

`llm/openai_client.py` sends `seed=LLM_SEED` on **every** Phase 1 call and drops it
only on a rejection, once, process-wide (`_SEED_SUPPORTED` / `seed_supported()` /
`_is_unsupported_seed`). That climb-down exists but nothing in the tree shows it has
ever fired, so `seed` is presumably still *accepted* on a plain (non-tool) request
against this deployment — untested for a request that also carries `tools`.

Separately, the comment block in `OpenAIClient.extract_json` records a measured fact
worth carrying into 06:

> "the service does not guarantee reproducibility even at temperature 0 with a fixed
> seed … **this deployment returns no `system_fingerprint`** … two warm runs of the
> chemspeed batch still differed on 10 of 100 rows"

So the expected Q3 answer is "`seed` accepted, `system_fingerprint` absent" — but that
is a prior, not evidence, and the tool-calling request is exactly the case never tried.

### Implications for ticket 04 (the tool contract)

Both branches of Q2 have to be planned for until the run comes back:

- **If `response_format` and `tools` compose** (turn 1 returns `tool_calls` with
  `content: null`, turn 2 returns parseable JSON): 04 is a thin extension. The tool
  loop reuses `OpenAIClient`'s existing JSON contract, `extract_json` stays the
  terminal step, and the only new surface is the tool-call turn plus the
  `role: "tool"` message. No change to the LLM layer's shape.
- **If they do not compose** (a 400 on the combined request): the tool layer needs a
  **second, separate call path** beside `call_openai`. Concretely — a tool-calling
  loop with `response_format` omitted, terminating in either (a) one final
  `response_format`-only call with no `tools` attached, to get the JSON verdict, or
  (b) a terminal `submit_answer` *tool* whose arguments carry the JSON, with
  `tool_choice` forced to it. Option (b) keeps one call shape but makes the final
  answer arrive as `function.arguments`, not `message.content`, which changes what 06
  caches and what 07's schema attaches to. Either way 04 can no longer be described as
  "`extract_json` with tools bolted on".

Q1's forced-named-`tool_choice` probe matters for the same reason: option (b) is only
available if a named `tool_choice` is honoured.

Q4 gates the loop's *shape*. With `parallel_tool_calls=false` honoured, 04 can specify
a strictly serial one-tool-per-turn loop, which is far easier to key deterministically
in 06 (the cache key stays a pure function of the transcript prefix). If parallel calls
cannot be suppressed, 06 must key a *set* of calls, and the ordering of that set becomes
a determinism hazard of exactly the kind `CLAUDE.md` warns about.

Q3 gates the determinism claim. If `seed` is rejected on a tool-calling request the
lane cannot inherit the `LLM_SEED=42` control at all, and — given the deployment
already returns no `system_fingerprint` — the only remaining reproducibility mechanism
for the agentic lane is the evidence cache in 06. That would make 06 load-bearing
rather than an optimisation.

### How to get the answers — the fallback path (HITL)

A complete probe script sits at
`.scratch/agentic-enrichment/scripts/01_tool_calling_spike.py`. It is read-only: it
imports `config` and `llm.openai_client` for endpoint/key/TLS/api-version resolution
and modifies no production code. It sends **seven small requests** (two-tool schema,
one-line prompt, entirely synthetic content — an invented `Zorblatt Widgetworks GmbH`
in `Fictionsburg`; no customer master data, no rows from `docs/`), prints the request
shape, the HTTP status, the error body verbatim on failure and the relevant response
fields on success, never prints the API key, and redacts the resource name from all
output. It aborts immediately — no retry loop, `max_retries=0` on the SDK client — on
a connection failure or a 401/403.

Probes: `Q1` tools + `tool_choice:"auto"`; `Q1b` forced named `tool_choice`;
`Q2` tools + `response_format` turn 1; `Q2b` the same request's turn 2 (synthetic tool
result fed back, final message JSON-parsed); `Q3` tools + `response_format` + `seed=42`,
reporting `system_fingerprint`; `Q4a` a two-tool prompt as control; `Q4b` the same
prompt with `parallel_tool_calls=False`, with the two tool-call counts compared.

Two details the script handles that a hand-rolled probe would get wrong and misread as
a Q2 failure:

1. `response_format={"type":"json_object"}` requires the literal word **"json"** in the
   messages or the service 400s for a reason that has nothing to do with `tools`. Both
   JSON-mode system prompts say it deliberately, so a 400 on Q2 can only mean the two
   parameters do not compose.
2. `gpt-5.x` reasoning deployments can reject a non-default `temperature`/`top_p`. That
   would 400 *every* probe and produce a false negative on Q2, so the script catches it
   once via `_is_unsupported_param`, drops both params for the rest of the run, and
   re-issues that one probe.

**Human: put the real key and endpoint in `.env`, then run this and paste the whole
output back.**

```powershell
cd C:\Users\ishav\Desktop\Intelligent-MDM-Pipeline
$env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe .scratch\agentic-enrichment\scripts\01_tool_calling_spike.py
```

If the credentials are still placeholders the script stops at preflight without sending
anything and says so.

The script itself was proven end to end against a local stub HTTP server before being
handed over — the 200 path (all seven probes, the JSON parse of turn 2, the Q4 count
comparison, the summary table), the verbatim-400 path, the 401 abort path and the
temperature climb-down path all execute correctly. What is untested is only the real
service's answers, which is the entire remaining question.

## ANSWERED — live run, 2026-08-29

The earlier "BLOCKED / HITL" note is superseded. `.env` did hold `.env.example` placeholders at the
time; real credentials were supplied and the spike was run. **All seven probes returned HTTP 200.**

Deployment `MDM-Apoorva-gpt-5.4`, model resolved to `gpt-5.4-2026-03-05`, on
`2024-08-01-preview`. `temperature=0.0` / `top_p=1.0` were accepted — the pinned determinism
constants survive tool-calling, no climb-down needed.

| # | question | answer |
|---|---|---|
| 1 | `tools` + `tool_choice` | **YES** — both `auto` and a forced named function |
| 2 | `response_format` **with** `tools` | **YES, they compose** |
| 3 | `seed` on a tool-calling request | **accepted (200)**, but `system_fingerprint = None` |
| 4 | `parallel_tool_calls=false` | **behaves** — control emitted 2 tool calls, flag emitted 1 |

**Q2 evidence** (the one that gates the tool contract), full two-turn round trip with
`response_format` present throughout:

```
turn 1: finish_reason='tool_calls', tool_calls=1
turn 2: content='{"legal_name":"Zorblatt Widgetworks GmbH","city":"Fictionsburg"}'
        JSON PARSE = OK
```

## What this means downstream

**Ticket 04 takes the thin branch.** A tool loop extends `extract_json` rather than needing a second
call path terminating in a `tools`-free `response_format` call or a forced `submit_answer` tool. The
fork that 06 and 07 were waiting on is closed.

**Q3's implication stands and is load-bearing.** `seed` is accepted but no `system_fingerprint`
comes back, matching the measured note in `OpenAIClient.extract_json` that two warm chemspeed runs
differed on 10/100 rows *with* the seed sent. There is **no service-side determinism signal**. For
any agent lane, `utils/cache.py` is therefore the only determinism control — which promotes
**ticket 06 from an optimisation to the load-bearing mechanism**.

Script: `.scratch/agentic-enrichment/scripts/01_tool_calling_spike.py`; captured output
`logs/spike/01_out.txt`. Synthetic content only.
