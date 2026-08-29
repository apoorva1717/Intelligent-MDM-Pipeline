"""The external request layer, made legible (ticket 21).

**OFF by default.** Enabled only when ``CALL_TRACE`` is truthy in the
environment. A human-readable line goes to the path in ``CALL_TRACE_OUT``
(default ``logs/call_trace.log``) and the same event as one JSON line to
``CALL_TRACE_JSON`` (default ``logs/call_trace.jsonl``) for aggregation.

Why this exists
---------------
Three failures on 2026-08-29 produced downstream evidence that was
**byte-identical to "this organisation does not exist"**, and not one of them
was visible in any log:

1. ``SERPAPI_KEY`` was shadowed by a duplicate placeholder in ``.env``, so every
   search silently fell back to DuckDuckGo.
2. ``serp_disk_key`` omitted the provider (ticket 20), so 251 empty DuckDuckGo
   results replayed as "no web presence" — no network call, no warning.
3. ``expand_abbreviations`` was applied to the ROR *scorer* but never to the ROR
   *query* (ticket 19), so ``Mass Inst of Tech`` went out verbatim while the
   expanded form sat unused in a local variable.

Two more of the same shape: ROR returns HTTP 500 on names containing ``/`` and
``1910 Genetics`` reaches ROR as ``Genetics`` (ticket 18).

Every one of those lives in the gap between *what the record says* and *what was
actually sent to whom*. Nothing recorded that. A measured conclusion — "~24-25%
is a coverage ceiling" — was drawn from a run whose retrieval was never
observed, and reported as a finding.

The outcome vocabulary is the point
-----------------------------------
Today four very different things collapse into one indistinguishable "no
match". They are separated here, and only here:

``PROVIDER_FAILED``
    The request could not be executed. A dropped TLS handshake is not evidence
    about the world. (:class:`search.base.SearchUnavailable`.)
``EMPTY``
    The provider answered, and the answer was "nothing".
``NO_CANDIDATE_MATCHED``
    Candidates came back; none of them was the entity being looked for.
``REJECTED_BY_GATE``
    The right candidate came back and a guard refused it — with which guard and
    at what score.
``OK``
    A value was taken.

Contract, deliberately narrow — the same one :mod:`enrichment.funnel_probe`
keeps:

* It never reads or writes a record, a flag, a provenance entry or a scoped
  field. It only appends to a file.
* It never influences a decision. Deleting every call site leaves the
  pipeline's behaviour bit-identical.
* It is inert unless ``CALL_TRACE`` is set, and the check is a module-level
  constant read once at import.
* **Nothing it writes may reach a cache key or a prompt.** It is an output
  artefact. ``tests/test_determinism.py`` asserts that structurally.
* Secrets never appear in a line.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

_TRUTHY = {"1", "true", "yes", "on"}

ENABLED: bool = (os.getenv("CALL_TRACE", "") or "").strip().lower() in _TRUTHY

_TEXT_PATH = os.getenv("CALL_TRACE_OUT", "logs/call_trace.log")
_JSON_PATH = os.getenv("CALL_TRACE_JSON", "logs/call_trace.jsonl")

# ── The outcome vocabulary ────────────────────────────────────────────────
OK = "ok"
EMPTY = "empty"
PROVIDER_FAILED = "provider_failed"
NO_CANDIDATE_MATCHED = "no_candidate_matched"
REJECTED_BY_GATE = "rejected_by_gate"
FROZEN = "frozen"

#: Substrings that must never appear in a traced value. A query string is
#: user data, not a secret, but a URL can carry one.
_REDACT_KEYS = ("api_key", "apikey", "key", "token", "secret", "password")

_lock = threading.Lock()
_text_fh: Any = None
_json_fh: Any = None


def _redact(params: "dict[str, Any] | None") -> "dict[str, Any]":
    """Query parameters with anything credential-shaped replaced."""
    out: dict[str, Any] = {}
    for name, value in (params or {}).items():
        lowered = str(name).lower()
        out[name] = "<redacted>" if any(k in lowered for k in _REDACT_KEYS) else value
    return out


def _open() -> None:
    global _text_fh, _json_fh
    for path, attr in ((_TEXT_PATH, "_text_fh"), (_JSON_PATH, "_json_fh")):
        if globals()[attr] is None:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            globals()[attr] = open(path, "a", encoding="utf-8")


def _render(event: "dict[str, Any]") -> str:
    """One readable line. The verbatim outbound string is the point of it, so
    it is never abbreviated away."""
    lane = event.get("lane", "?")
    parts = [f"[{lane}]"]
    if event.get("provider"):
        parts.append(str(event["provider"]))
    if event.get("query") is not None:
        parts.append(f'q="{event["query"]}"')
    if event.get("url"):
        parts.append(str(event["url"]))
    # A registry request carries its query in the PARAMS, and that string is
    # the whole point of the trace -- ticket 19's defect was `Mass Inst of
    # Tech` going out verbatim while the expanded form sat in a local
    # variable. A line showing only the endpoint would have hidden it.
    for name in ("affiliation", "query", "filter[entity.legalName]", "q"):
        value = (event.get("params") or {}).get(name)
        if value is not None:
            parts.append(f'{name}="{value}"')
    parts.append("cache=" + ("HIT" if event.get("cache_hit") else "MISS"))
    parts.append(f"-> {event.get('outcome', '?')}")
    for field in ("results", "candidates", "status", "gate", "score", "chosen",
                  "reason"):
        if event.get(field) is not None:
            parts.append(f"{field}={event[field]}")
    if event.get("record_id"):
        parts.append(f"({event['record_id']})")
    return " ".join(parts)


def call(
    lane: str,
    *,
    outcome: str,
    query: str | None = None,
    url: str | None = None,
    params: "dict[str, Any] | None" = None,
    provider: str | None = None,
    cache_hit: bool = False,
    **fields: Any,
) -> None:
    """Record one external request. No-op unless ``CALL_TRACE`` is set.

    *query* is the **verbatim outbound string**, never the record's input
    name — the difference between those two is where three of the five
    failures above live, and a trace that logged the input would have shown
    nothing wrong in any of them.
    """
    if not ENABLED:
        return
    from utils.cache import current_record_id

    event: dict[str, Any] = {
        "record_id": current_record_id.get(),
        "lane": lane,
        "outcome": outcome,
        "cache_hit": cache_hit,
    }
    if query is not None:
        event["query"] = query
    if url:
        event["url"] = url
    if params:
        event["params"] = _redact(params)
    if provider:
        event["provider"] = provider
    event.update(fields)

    with _lock:
        _open()
        _text_fh.write(_render(event) + "\n")
        _text_fh.flush()
        _json_fh.write(json.dumps(event, default=str) + "\n")
        _json_fh.flush()


def manifest(fields: "dict[str, Any]") -> str:
    """Render the run manifest — what was **actually** live, not configured.

    Returned as a string rather than written, so the caller decides where it
    belongs (stdout for an interactive run, the trace file for a batch). Every
    one of 2026-08-29's three silent failures would have been caught on sight
    in this block, which is the entire argument for it.

    Unlike :func:`call` this is NOT gated on ``CALL_TRACE``: a run that cannot
    say what it was talking to is the problem, and printing eight lines costs
    nothing.
    """
    width = max((len(k) for k in fields), default=0)
    # ASCII only: this is printed to a console, and the default Windows
    # code page (cp1252) cannot encode box-drawing characters.
    lines = ["== run manifest =="]
    for key, value in fields.items():
        lines.append(f"  {key.ljust(width)}  {value}")
    return "\n".join(lines)


def describe_run(settings: Any, search_client: Any = None) -> "dict[str, Any]":
    """Assemble the manifest's contents from what the process actually holds.

    Reads the *resolved* objects, not the configuration that was meant to
    produce them — `serp_provider` comes from the client that will answer, not
    from whether ``SERPAPI_KEY`` looked set.
    """
    from utils.cache import active_evidence_cache

    out: dict[str, Any] = {}

    if search_client is not None:
        from search.base import provider_id_of
        out["serp_provider"] = provider_id_of(search_client)
    else:
        out["serp_provider"] = "<not built>"

    out["mock_external_calls"] = bool(
        getattr(settings, "mock_external_calls", False),
    )
    out["cache_frozen"] = bool(getattr(settings, "cache_frozen", False))
    out["evidence_cache_dir"] = (
        getattr(settings, "evidence_cache_dir", "") or "<memory only>"
    )
    # `openai_model` IS the Azure deployment name -- the field is named for
    # the OpenAI-compatible concept, not the Azure one, and guessing
    # `azure_openai_deployment` reported "<unset>" on a correctly configured
    # run. Exactly the class of thing this block exists to expose.
    out["llm_deployment"] = getattr(settings, "openai_model", "") or "<unset>"
    out["llm_api_version"] = os.getenv("AZURE_OPENAI_API_VERSION", "") or "<unset>"

    endpoint = getattr(settings, "azure_openai_endpoint", "") or ""
    out["llm_endpoint"] = endpoint or "<unset>"
    # Presence only. The key itself must never reach a trace or a console.
    out["llm_key_present"] = bool(
        (getattr(settings, "openai_api_key", "") or "").strip(),
    )

    cache = active_evidence_cache()
    if cache is not None and cache.root is not None:
        for namespace in ("serp", "registry", "fetch", "llm", "page_reads",
                          "wikidata"):
            directory = cache.root / namespace
            if directory.is_dir():
                out[f"cache[{namespace}]"] = sum(1 for _ in directory.iterdir())

    out["git_sha"] = _git_sha()
    return out


def _git_sha() -> str:
    """The tree the run was made from. Unknown is an honest answer; a run that
    cannot say which code produced it is not reproducible, and saying so is
    better than omitting the line."""
    import subprocess
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip() or "<unknown>"
    except Exception:  # noqa: BLE001
        return "<unknown>"
