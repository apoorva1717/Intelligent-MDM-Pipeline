r"""Ticket 01 — deployment tool-calling capability spike.

Answers four questions with an ACTUAL request/response against the configured
Azure OpenAI deployment (never with documentation):

  Q1  Does the deployment accept ``tools`` + ``tool_choice`` at all, on the
      pinned API version?
  Q2  Can ``response_format={"type": "json_object"}`` ride along WITH ``tools``
      in ONE request — and does the two-turn loop (tool call -> tool result ->
      final JSON answer) complete?  This is the one that matters: the whole of
      ``llm/openai_client.py`` is built on ``response_format``.
  Q3  Is ``seed`` still accepted on a tool-calling request, and does
      ``system_fingerprint`` come back?
  Q4  Does ``parallel_tool_calls=false`` behave as documented?

Run it:

    cd <repo root>
    $env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe .scratch\agentic-enrichment\scripts\01_tool_calling_spike.py

Notes
-----
* READ-ONLY spike. It imports ``config`` and ``llm.openai_client`` for the
  endpoint / key / TLS / api-version resolution and does not modify anything.
* Six small requests, a two-tool schema and a one-line prompt. Not a batch.
* Every string sent to the model is SYNTHETIC and invented for this script.
  No customer master data, no rows from ``docs/``, no real company records.
* The API key is never printed; the endpoint host is redacted in all output.
* ``MOCK_EXTERNAL_CALLS`` is pinned to ``false`` before ``config`` is imported,
  so the probes cannot silently be answered by a mock.
* Nothing retries in a loop. A connection failure or a 401/403 aborts the whole
  run immediately with a HITL message.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

# --- pin the environment BEFORE config/dotenv is imported --------------------
# load_dotenv() does not override an existing os.environ entry, so setting these
# first makes them win over anything in .env, and over anything a test conftest
# might have left behind in an inherited shell.
os.environ["MOCK_EXTERNAL_CALLS"] = "false"
os.environ["CACHE_FROZEN"] = "false"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

try:  # non-ASCII on the Windows console
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import config  # noqa: E402,F401  (imported for its load_dotenv side effect)
from llm.openai_client import (  # noqa: E402
    DEFAULT_AZURE_OPENAI_API_VERSION,
    LLM_SEED,
    LLM_TEMPERATURE,
    LLM_TOP_P,
    _is_unsupported_param,
    get_openai_client,
    seed_supported,
)

import openai  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic fixture — invented for this spike, resembles nothing real.
# ---------------------------------------------------------------------------

ORG = "Zorblatt Widgetworks GmbH"
CITY = "Fictionsburg"

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_registry",
            "description": "Look up an organisation in a company registry.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Legal name."},
                    "city": {"type": "string", "description": "City."},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": "Fetch the text of a web page.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
]

# `response_format={"type":"json_object"}` requires the literal word "json"
# somewhere in the messages, or the service returns a 400 that has nothing to do
# with tools. Both JSON-mode system prompts below say it on purpose, so a 400
# can only mean "json_object and tools do not compose".
SYS_TOOLS_ONLY = "You are a resolver. Use the tools when they help."
SYS_TOOLS_JSON = (
    "You are a resolver. Use the tools when they help. "
    'When you answer in text, answer as a JSON object of the shape '
    '{"legal_name": string, "city": string}.'
)
USER_ONE = f"Resolve the organisation {ORG!r} in {CITY}."
USER_TWO = (
    f"Do both of these: look up {ORG!r} in the registry, and fetch "
    "https://zorblatt-widgetworks.invalid/about."
)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

_ENDPOINT_HOST = ""


def redact(text: str) -> str:
    """Strip the resource name and anything key-shaped out of *text*."""
    out = text
    if _ENDPOINT_HOST:
        out = out.replace(_ENDPOINT_HOST, "<redacted-resource>")
    key = os.getenv("AZURE_OPENAI_API_KEY") or ""
    if key:
        out = out.replace(key, "<redacted-key>")
    return out


def rule(char: str = "-") -> None:
    print(char * 78)


def show_request(label: str, params: dict[str, Any]) -> None:
    rule("=")
    print(f"PROBE: {label}")
    rule()
    shown = dict(params)
    # tool schemas are printed once, at the top of the run; keep probes readable
    if "tools" in shown:
        shown["tools"] = f"<{len(params['tools'])} tools: " + ", ".join(
            t["function"]["name"] for t in params["tools"]
        ) + ">"
    print("REQUEST:")
    print(redact(json.dumps(shown, indent=2, ensure_ascii=False, default=str)))


def show_error(exc: Exception) -> str:
    """Print status + verbatim error body. Returns a short verdict token."""
    if isinstance(exc, openai.APIStatusError):
        print(f"HTTP STATUS: {exc.status_code}")
        body = ""
        try:
            body = exc.response.text
        except Exception:  # noqa: BLE001
            body = str(exc)
        print("ERROR BODY (verbatim):")
        print(redact(body))
        return f"HTTP {exc.status_code}"
    if isinstance(exc, openai.APIConnectionError):
        print("HTTP STATUS: <no response — connection failed>")
        print("ERROR (verbatim):")
        print(redact(repr(exc)))
        return "CONNECTION FAILED"
    print("HTTP STATUS: <no response>")
    print("ERROR (verbatim):")
    print(redact(repr(exc)))
    return type(exc).__name__


def show_response(resp: Any) -> None:
    print("HTTP STATUS: 200")
    choice = resp.choices[0]
    msg = choice.message
    calls = msg.tool_calls or []
    print(f"  model              = {resp.model}")
    print(f"  system_fingerprint = {getattr(resp, 'system_fingerprint', None)!r}")
    print(f"  finish_reason      = {choice.finish_reason!r}")
    print(f"  message.content    = {redact(repr(msg.content))}")
    print(f"  tool_calls         = {len(calls)}")
    for i, c in enumerate(calls):
        print(f"    [{i}] id={c.id} name={c.function.name} args={c.function.arguments}")
    usage = getattr(resp, "usage", None)
    if usage is not None:
        print(
            f"  usage              = prompt={usage.prompt_tokens} "
            f"completion={usage.completion_tokens} total={usage.total_tokens}"
        )


class Abort(RuntimeError):
    """Unreachable endpoint / rejected key — stop, do not work around it."""


RESULTS: list[tuple[str, str]] = []

#: gpt-5.x reasoning deployments reject a non-default `temperature`/`top_p`.
#: If that happens it would poison EVERY probe and give a false negative on the
#: question we actually care about (Q2), so it is caught ONCE, process-wide, the
#: two params are dropped, and the probe is re-issued. One bounded re-issue —
#: not a retry loop. Same shape as `call_openai`'s one-shot `seed` climb-down.
_SAMPLING_PARAMS_SUPPORTED = True


async def probe(
    client: Any, label: str, params: dict[str, Any]
) -> Any | None:
    global _SAMPLING_PARAMS_SUPPORTED
    if not _SAMPLING_PARAMS_SUPPORTED:
        params = {k: v for k, v in params.items() if k not in ("temperature", "top_p")}
    show_request(label, params)
    try:
        resp = await client.chat.completions.create(**params)
    except Exception as exc:  # noqa: BLE001
        if (
            _SAMPLING_PARAMS_SUPPORTED
            and ("temperature" in params or "top_p" in params)
            and _is_unsupported_param(exc, "temperature", "top_p")
        ):
            _SAMPLING_PARAMS_SUPPORTED = False
            print("HTTP STATUS: 400 — deployment rejected temperature/top_p:")
            print(redact(str(exc)))
            print(">>> dropping temperature/top_p for the rest of the run and "
                  "re-issuing this probe (once).")
            return await probe(client, label + " [no temperature/top_p]", params)
        verdict = show_error(exc)
        RESULTS.append((label, f"FAILED — {verdict}"))
        if isinstance(exc, openai.APIConnectionError):
            raise Abort("endpoint unreachable") from exc
        if isinstance(exc, openai.APIStatusError) and exc.status_code in (401, 403):
            raise Abort(f"credentials rejected ({exc.status_code})") from exc
        return None
    show_response(resp)
    RESULTS.append((label, "OK (200)"))
    return resp


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

_PLACEHOLDERS = {
    "your-azure-key-here",
    "your-openai-api-key",
    "https://your-resource.openai.azure.com/",
    "https://your-resource.openai.azure.com",
    "",
}


def preflight() -> tuple[str, str]:
    global _ENDPOINT_HOST
    key = (os.getenv("AZURE_OPENAI_API_KEY") or "").strip()
    endpoint = (os.getenv("AZURE_OPENAI_ENDPOINT") or "").strip()
    deployment = (os.getenv("AZURE_OPENAI_DEPLOYMENT") or "gpt-5.4").strip()
    api_version = (
        os.getenv("AZURE_OPENAI_API_VERSION") or DEFAULT_AZURE_OPENAI_API_VERSION
    ).strip()

    if endpoint:
        _ENDPOINT_HOST = endpoint.split("//", 1)[-1].split("/", 1)[0]

    rule("=")
    print("TICKET 01 — Azure OpenAI tool-calling capability spike")
    rule("=")
    print(f"  endpoint            = https://<redacted-resource>{'/' if endpoint.endswith('/') else ''}")
    print(f"  deployment (model)  = {deployment}")
    print(f"  api_version         = {api_version}")
    print(f"  api key             = {'present (' + str(len(key)) + ' chars)' if key else 'MISSING'}")
    print(f"  MOCK_EXTERNAL_CALLS = {os.getenv('MOCK_EXTERNAL_CALLS')!r} (pinned by this script)")
    print(f"  openai sdk          = {openai.__version__}")
    print(f"  determinism consts  = temperature={LLM_TEMPERATURE} top_p={LLM_TOP_P} "
          f"seed={LLM_SEED} seed_supported()={seed_supported()}")
    print()
    print("  tool schema sent on every probe:")
    print("  " + json.dumps(TOOLS, indent=2).replace("\n", "\n  "))
    print()

    if key.lower() in _PLACEHOLDERS or endpoint.lower() in _PLACEHOLDERS:
        rule("!")
        print("PREFLIGHT FAILED — .env holds the .env.example PLACEHOLDER values,")
        print("not real credentials. No request was sent.")
        print()
        print("Put the real AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT into")
        print(".env (or export them in the shell) and re-run this exact command:")
        print()
        print('  cd C:\\Users\\ishav\\Desktop\\Intelligent-MDM-Pipeline')
        print('  $env:PYTHONIOENCODING="utf-8"; '
              '.venv\\Scripts\\python.exe .scratch\\agentic-enrichment\\scripts\\01_tool_calling_spike.py')
        rule("!")
        raise SystemExit(2)

    if not key or not endpoint:
        rule("!")
        print("PREFLIGHT FAILED — AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT unset.")
        rule("!")
        raise SystemExit(2)

    return deployment, api_version


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


async def main() -> int:
    model, api_version = preflight()
    # max_retries=0: the SDK retries connection errors twice by default, and a
    # capability spike must not silently loop against the user's deployment.
    client = get_openai_client().with_options(max_retries=0)

    base: dict[str, Any] = {
        "model": model,
        "max_completion_tokens": 300,
        "temperature": LLM_TEMPERATURE,
        "top_p": LLM_TOP_P,
    }

    try:
        # -- Q1 ------------------------------------------------------------
        # tools + tool_choice, no response_format. Baseline capability.
        await probe(
            client,
            "Q1 · tools + tool_choice='auto' (no response_format)",
            {
                **base,
                "messages": [
                    {"role": "system", "content": SYS_TOOLS_ONLY},
                    {"role": "user", "content": USER_ONE},
                ],
                "tools": TOOLS,
                "tool_choice": "auto",
            },
        )

        # Q1b — a *forced* named tool_choice is a stricter test than "auto".
        await probe(
            client,
            "Q1b · tool_choice={'type':'function','function':{'name':'lookup_registry'}}",
            {
                **base,
                "messages": [
                    {"role": "system", "content": SYS_TOOLS_ONLY},
                    {"role": "user", "content": USER_ONE},
                ],
                "tools": TOOLS,
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "lookup_registry"},
                },
            },
        )

        # -- Q2 — THE ONE THAT MATTERS --------------------------------------
        # response_format=json_object in the SAME request as tools.
        turn1 = await probe(
            client,
            "Q2 · tools + response_format={'type':'json_object'} — turn 1",
            {
                **base,
                "messages": [
                    {"role": "system", "content": SYS_TOOLS_JSON},
                    {"role": "user", "content": USER_ONE},
                ],
                "tools": TOOLS,
                "tool_choice": "auto",
                "response_format": {"type": "json_object"},
            },
        )

        # Q2b — close the loop: feed a synthetic tool result back and check the
        # final assistant message is (a) produced and (b) parseable JSON.
        if turn1 is not None and (turn1.choices[0].message.tool_calls or []):
            call = turn1.choices[0].message.tool_calls[0]
            assistant_msg = {
                "role": "assistant",
                "content": turn1.choices[0].message.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                ],
            }
            tool_result = json.dumps(
                {"found": True, "legal_name": ORG, "city": CITY}
            )
            turn2 = await probe(
                client,
                "Q2b · same request, turn 2 (tool result -> final JSON answer)",
                {
                    **base,
                    "messages": [
                        {"role": "system", "content": SYS_TOOLS_JSON},
                        {"role": "user", "content": USER_ONE},
                        assistant_msg,
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": tool_result,
                        },
                    ],
                    "tools": TOOLS,
                    "tool_choice": "auto",
                    "response_format": {"type": "json_object"},
                },
            )
            if turn2 is not None:
                content = turn2.choices[0].message.content
                try:
                    parsed = json.loads(content or "")
                    print(f"  JSON PARSE         = OK -> {parsed!r}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  JSON PARSE         = FAILED ({exc})")
        else:
            print("  (turn 1 produced no tool_calls — turn 2 skipped)")

        # -- Q3 --------------------------------------------------------------
        # seed on a tool-calling request; does system_fingerprint come back?
        await probe(
            client,
            f"Q3 · tools + response_format + seed={LLM_SEED}",
            {
                **base,
                "messages": [
                    {"role": "system", "content": SYS_TOOLS_JSON},
                    {"role": "user", "content": USER_ONE},
                ],
                "tools": TOOLS,
                "tool_choice": "auto",
                "response_format": {"type": "json_object"},
                "seed": LLM_SEED,
            },
        )

        # -- Q4 --------------------------------------------------------------
        # A prompt that explicitly invites two tool calls, run twice.
        control = await probe(
            client,
            "Q4a · control — two-tool prompt, parallel_tool_calls omitted",
            {
                **base,
                "messages": [
                    {"role": "system", "content": SYS_TOOLS_ONLY},
                    {"role": "user", "content": USER_TWO},
                ],
                "tools": TOOLS,
                "tool_choice": "auto",
            },
        )
        forced = await probe(
            client,
            "Q4b · same prompt, parallel_tool_calls=False",
            {
                **base,
                "messages": [
                    {"role": "system", "content": SYS_TOOLS_ONLY},
                    {"role": "user", "content": USER_TWO},
                ],
                "tools": TOOLS,
                "tool_choice": "auto",
                "parallel_tool_calls": False,
            },
        )
        if control is not None and forced is not None:
            n_ctrl = len(control.choices[0].message.tool_calls or [])
            n_forced = len(forced.choices[0].message.tool_calls or [])
            print(f"  Q4 VERDICT         = control emitted {n_ctrl} tool call(s); "
                  f"parallel_tool_calls=False emitted {n_forced}")

    except Abort as exc:
        rule("!")
        print(f"ABORTED — {exc}. Not retrying, not working around it.")
        print("This is the HITL fallback: re-run this exact command yourself and")
        print("paste the output back:")
        print()
        print('  cd C:\\Users\\ishav\\Desktop\\Intelligent-MDM-Pipeline')
        print('  $env:PYTHONIOENCODING="utf-8"; '
              '.venv\\Scripts\\python.exe .scratch\\agentic-enrichment\\scripts\\01_tool_calling_spike.py')
        rule("!")
        return 1
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass

    rule("=")
    print("SUMMARY")
    rule()
    for label, verdict in RESULTS:
        print(f"  {verdict:<24} {label}")
    rule("=")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
