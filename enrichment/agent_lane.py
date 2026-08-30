"""A bounded agentic resolver — the model plans, the registries decide.

This is the lane the map's decisions 5-12 describe, built as a standalone
resolver so it can be baked off against the current pipeline before anything is
wired into `orchestrator.py`.

**Why an agent at all.** Ticket 11 measured where records die: not in the gates
but in the *query*. The registry holds the entity and the query never finds it —
`MI Department Of Health & Human Servic` against michigan.gov, `SLAC` against
Stanford, `UTSW` against UT Southwestern. Choosing a better query is a planning
problem, and it is the one thing in this pipeline a fixed rule cannot do,
because the right next query depends on what the last one returned.

**Why it is still safe.** Four properties, and none of them is a prompt:

1. **Planner, not author.** The agent picks queries and tools. It never writes
   an output field. The only way a name or an identifier leaves this lane is
   through :func:`propose_and_verify`, which re-queries a *register* and
   returns that register's answer. A proposal no register confirms is refused.
2. **The comparators are not on the tool surface.** `registry_match`,
   `locality` and every threshold stay unreachable from inside the loop. A
   model that proposes *and* verifies is one source, not two.
3. **No uncached tool, ever.** Every tool here routes through the same clients
   the orchestrator uses, which route through `utils.cache`. A second run over
   the same records issues no network calls, so `tools/run_diff.py` still holds.
4. **"Insufficient evidence" is a first-class success.** The loop can end by
   declining, and a decline is not a failure — it is the honest outcome for a
   record no register carries, which ticket 11 measured as most of the residual.

**Budget.** 8 steps, hard. Ticket 09 found anchoring converges around step 4
and long context degrades accuracy 0.92 -> 0.68, so a bigger budget buys
noise. The gate that admits records to the lane is the cost lever, not this.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from enrichment.identity import same_organisation
from llm.openai_client import LLM_SEED, LLM_TEMPERATURE, seed_supported
from utils.cache import cached_serp

logger = logging.getLogger(__name__)

MAX_STEPS = 8
MAX_CANDIDATES = 5
SNIPPET_CHARS = 300


# ---------------------------------------------------------------------------
# The tool surface
# ---------------------------------------------------------------------------
#
# Structured arguments, not a free-text query blob: the agent chooses the NAME
# it searches for — which is the decision worth giving it — while locality is
# passed through structurally so it cannot be dropped, mangled, or smuggled
# into the name. Ticket 11 measured that dropping location raises `chosen>=0.8`
# from 2 to 7 and that 6 of those 7 are WRONG, so location is suppressing false
# positives and the agent does not get to turn it off.

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "ror_lookup",
            "description": (
                "Search the ROR registry of research organisations for a name. "
                "Use for universities, hospitals, institutes, government "
                "agencies and national laboratories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "The organisation name to search for. Try the "
                            "expansion of an acronym here."
                        ),
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gleif_lookup",
            "description": (
                "Search the GLEIF LEI register of legally registered entities "
                "for a name. Use for companies."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web. Use to find out what an abbreviation or "
                "acronym stands for before looking it up in a register."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_and_verify",
            "description": (
                "Propose the organisation name you believe this record names. "
                "It is checked against ROR and GLEIF and you are told whether "
                "they confirm it. This is the ONLY way to produce an answer. "
                "If it comes back unconfirmed you may search again and "
                "re-propose."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
                "required": ["name", "reasoning"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "insufficient_evidence",
            "description": (
                "Declare that the evidence does not identify this "
                "organisation. This is a correct and expected outcome for a "
                "record no register carries — prefer it to a guess."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {"type": "string"},
                },
                "required": ["reasoning"],
                "additionalProperties": False,
            },
        },
    },
]


SYSTEM_PROMPT = (
    "You identify the organisation a customer master-data record names.\n\n"
    "The record's own Name 1 is often abbreviated, truncated by a fixed-width "
    "field, or written in an internal shorthand. Your job is to work out which "
    "real organisation it means and to get a registry to confirm it.\n\n"
    "How to work:\n"
    "- If the name contains an acronym or abbreviation you cannot resolve, use "
    "web_search FIRST to find what it stands for, then look up the expansion.\n"
    "- Search the register that fits: ror_lookup for universities, hospitals, "
    "institutes, agencies and national laboratories; gleif_lookup for "
    "companies.\n"
    "- A registry search returns candidates with a match score and a location. "
    "A candidate in a different city or state from the record is usually a "
    "different organisation with a similar name.\n"
    "- When you have a name you believe in, call propose_and_verify. If it is "
    "not confirmed, that is information: search differently and re-propose.\n"
    "- If nothing identifies the organisation, call insufficient_evidence. "
    "That is a correct answer, not a failure. Never invent a name to have "
    "something to say.\n\n"
    "You cannot write to the record. Only a registry confirmation produces an "
    "answer."
)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """What the lane concluded, and how it got there."""

    name: str | None = None
    registry: str | None = None
    identifier: str | None = None
    #: True only when a register confirmed the proposal.
    verified: bool = False
    declined: bool = False
    reasoning: str = ""
    steps: int = 0
    #: Every tool call, in order, for the trace.
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def _trim_ror(res: dict[str, Any]) -> dict[str, Any]:
    """ROR's answer, trimmed to what a planner needs to choose a next step.

    The scored candidate list rather than the raw payload or the bare winner:
    raw is unaffordable in tokens, and handing back only the winner would make
    the *selection* invisible to the agent, which is the one decision it is
    here to improve.
    """
    if res.get("error"):
        return {"status": "unavailable", "detail": str(res.get("error"))[:120]}
    if not res.get("matched"):
        return {
            "status": "no_match",
            "rejected": [
                {
                    "name": r.get("candidate_name"),
                    "why_rejected": r.get("guard") or r.get("detail"),
                }
                for r in (res.get("guard_rejections") or [])[:MAX_CANDIDATES]
            ],
        }
    return {
        "status": "match",
        "name": res.get("official_name"),
        "ror_id": res.get("ror_id"),
        "score": res.get("score"),
        "country": res.get("country"),
        "org_types": list(res.get("org_types") or ())[:4],
        "also_published_as": list(res.get("name_variants") or ())[:MAX_CANDIDATES],
    }


def _trim_gleif(res: dict[str, Any]) -> dict[str, Any]:
    if res.get("error"):
        return {"status": "unavailable", "detail": str(res.get("error"))[:120]}
    if not res.get("matched"):
        return {"status": "no_match"}
    return {
        "status": "match",
        "legal_name": res.get("legal_name"),
        "lei_id": res.get("lei_id"),
        "score": res.get("score"),
        "also_published_as": list(res.get("entity_names") or ())[:MAX_CANDIDATES],
    }


def _trim_serp(results: Any) -> dict[str, Any]:
    """Search results, trimmed. `SearchUnavailable` must reach the agent as
    `unavailable`, never as `no_results` — reading an outage as "this
    organisation has no web presence" is the exact error the cache layer
    already refuses to make."""
    if results is None:
        return {"status": "unavailable"}
    items = []
    for r in list(results)[:MAX_CANDIDATES]:
        get = r.get if isinstance(r, dict) else (lambda k, d=None: getattr(r, k, d))
        items.append({
            "title": get("title") or "",
            "url": get("link") or get("url") or "",
            "snippet": (get("snippet") or "")[:SNIPPET_CHARS],
        })
    if not items:
        return {"status": "no_results"}
    return {"status": "ok", "results": items}


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

async def _verify(name: str, ror_client, lei_client, ctx) -> dict[str, Any]:
    """Re-query the registers for *name* — the lane's only route to an answer.

    This is a TOOL rather than a final message on purpose (ticket 04 Q4): the
    agent sees the verdict, so an unconfirmed proposal is information it can act
    on with a different query rather than a dead end. That retry IS the lane's
    value.

    The comparison itself happens inside the registry clients, where it always
    has. Nothing here re-scores, re-thresholds or second-guesses them.
    """
    out: dict[str, Any] = {"proposed": name}

    # The drift guard, BEFORE the register is asked. A register confirms
    # `Harvard University` for a record that says `Wyss Inst` because Harvard
    # exists — verification catches fabrication and cannot catch a wander to a
    # real neighbour. In customer master data that is the more dangerous error:
    # silently replacing a customer with its parent or its acquirer reads as
    # successful enrichment. Refusing here rather than after the lookup also
    # tells the agent WHY, so its next proposal is better informed.
    verdict = same_organisation(ctx.get("record_name"), name, "not_drifted")
    if not verdict.same:
        out.update(
            confirmed=False,
            detail=(
                "That names a different organisation from the record. "
                f"{verdict.reason}. Propose a name that keeps what the record "
                "actually says, or call insufficient_evidence."
            ),
        )
        return out

    try:
        ror = await ror_client.call(
            name, country_code=ctx.get("country_code"),
            country=ctx.get("country"), city=ctx.get("city"),
            state=ctx.get("state"),
        )
    except Exception as exc:  # noqa: BLE001
        ror = {"error": str(exc)}
    if ror.get("matched"):
        out.update(
            confirmed=True, registry="ROR",
            registry_name=ror.get("official_name"),
            identifier=ror.get("ror_id"), score=ror.get("score"),
        )
        return out

    try:
        lei = await lei_client.call(
            name, country_code=ctx.get("country_code"),
            city=ctx.get("city"), state=ctx.get("state"),
        )
    except Exception as exc:  # noqa: BLE001
        lei = {"error": str(exc)}
    if lei.get("matched"):
        out.update(
            confirmed=True, registry="GLEIF",
            registry_name=lei.get("legal_name"),
            identifier=lei.get("lei_id"), score=lei.get("score"),
        )
        return out

    out.update(
        confirmed=False,
        detail=(
            "Neither ROR nor GLEIF confirms this name at this location. Either "
            "the name is wrong, or this organisation is in no register - if you "
            "have searched and found nothing, say so with insufficient_evidence."
        ),
    )
    return out


async def run_agent_lane(
    *,
    record_id: str,
    name1: str,
    name2: str | None = None,
    city: str | None = None,
    state: str | None = None,
    country: str | None = None,
    country_code: str | None = None,
    openai_client,
    ror_client,
    lei_client,
    search_client,
    cache=None,
    deployment: str | None = None,
    max_steps: int = MAX_STEPS,
) -> AgentResult:
    """Plan a resolution for one record. Never writes anything."""
    import os

    res = AgentResult()
    ctx = {"city": city, "state": state, "country": country,
           "country_code": country_code, "record_name": name1}

    user = (
        "Name 1: " + str(name1) + "\n"
        "Name 2: " + str(name2 or "(none)") + "\n"
        "Location: " + str(city or "(not stated)") + ", "
        + str(state or "(not stated)") + ", "
        + str(country or "(not stated)") + "\n\n"
        "Which organisation does this record name?"
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    model = deployment or os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4")

    for step in range(max_steps):
        res.steps = step + 1
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "temperature": LLM_TEMPERATURE,
        }
        if seed_supported():
            params["seed"] = LLM_SEED
        try:
            reply = await openai_client.chat.completions.create(**params)
        except Exception as exc:  # noqa: BLE001 - a lane may never fail a record
            res.error = type(exc).__name__ + ": " + str(exc)[:200]
            logger.info("[%s] agent lane: LLM failed: %s", record_id, exc)
            return res

        msg = reply.choices[0].message
        calls = list(getattr(msg, "tool_calls", None) or ())
        if not calls:
            # The model answered in prose. It has no route to an answer that
            # way - only propose_and_verify produces one - so this is a
            # decline, not a result.
            res.declined = True
            res.reasoning = (msg.content or "").strip()[:400]
            return res

        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name,
                              "arguments": c.function.arguments}}
                for c in calls
            ],
        })

        for call in calls:
            fn = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            res.trajectory.append({"step": res.steps, "tool": fn, "args": args})

            if fn == "ror_lookup":
                try:
                    raw = await ror_client.call(
                        args.get("name") or name1,
                        country_code=country_code, country=country,
                        city=city, state=state,
                    )
                except Exception as exc:  # noqa: BLE001
                    raw = {"error": str(exc)}
                payload = _trim_ror(raw)

            elif fn == "gleif_lookup":
                try:
                    raw = await lei_client.call(
                        args.get("name") or name1,
                        country_code=country_code, city=city, state=state,
                    )
                except Exception as exc:  # noqa: BLE001
                    raw = {"error": str(exc)}
                payload = _trim_gleif(raw)

            elif fn == "web_search":
                try:
                    hits = await cached_serp(
                        cache, search_client, args.get("query") or name1,
                        num_results=MAX_CANDIDATES, country=country,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.info("[%s] agent web_search failed: %s",
                                record_id, exc)
                    hits = None
                payload = _trim_serp(hits)

            elif fn == "propose_and_verify":
                proposed = str(args.get("name") or "").strip()
                payload = await _verify(proposed, ror_client, lei_client, ctx)
                if payload.get("confirmed"):
                    # The REGISTRY's spelling, never the model's proposal.
                    res.name = payload.get("registry_name")
                    res.registry = payload.get("registry")
                    res.identifier = payload.get("identifier")
                    res.verified = True
                    res.reasoning = str(args.get("reasoning") or "")[:400]
                    res.trajectory.append(
                        {"step": res.steps, "tool": "RESULT",
                         "args": {"name": res.name}},
                    )
                    return res

            elif fn == "insufficient_evidence":
                res.declined = True
                res.reasoning = str(args.get("reasoning") or "")[:400]
                return res

            else:
                payload = {"error": "unknown tool " + str(fn)}

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(payload, default=str)[:4000],
            })

    # Budget exhausted without a confirmation. That is a decline.
    res.declined = True
    res.reasoning = "step budget (" + str(max_steps) + ") exhausted"
    return res
