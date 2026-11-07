"""The research log columns of a looked-up row, and what the lookup learned
about the entity itself (dissolved, merged, renamed, a parent's or a
sibling's number …).

Everything here is read from the entity's usage record (the same record
``tools/vat_validate_xlsx.py --usage-out`` writes) and the upload's other
records: no call is made, so the columns cost nothing. They describe what the
lookup saw, not a registry check: a company the pages fetched say nothing
about is not reported as dissolved.

``VAT_LOG_COLUMNS`` (default on) adds them to the endpoint's and the batch
runner's output, after the cost columns.
"""

from __future__ import annotations

import json
import re
from typing import Any

from vat.lookup import NO_VAT_INFERRED, NO_VAT_STATED, FoundResult

#: Appended after the cost columns when log columns are on.
LOG_COLUMNS: list[str] = [
    "VAT_Found_Entity_Status",
    "VAT_Found_Name_vs_SAP",
    "VAT_Found_Evidence_Snippet",
    "VAT_Log_Name_Class",
    "VAT_Log_Search_Name",
    "VAT_Log_Queries",
    "VAT_Log_Ranking",
    "VAT_Log_Hosts",
    "VAT_Log_Pages",
    "VAT_Log_Not_Fetched",
    "VAT_Log_Dropped",
    "VAT_Log_Operator",
    "VAT_Log_Peppol",
    "VAT_Log_Aggregator",
    "VAT_Log_No_Number_Class",
    "VAT_Log_Agent",
    "VAT_Log_Counts",
]
BLANK_LOG_COLUMNS: dict[str, Any] = dict.fromkeys(LOG_COLUMNS)

#: Excel's cell limit is 32,767 characters; a log cell stays well under it.
CELL_CHARS = 4000

# ── VAT_Found_Entity_Status codes ───────────────────────────────────────────
INSOLVENT = "INSOLVENT"
IN_LIQUIDATION = "IN_LIQUIDATION"
DISSOLVED = "DISSOLVED"
MERGED = "MERGED"
RENAMED = "RENAMED"
SUCCESSOR = "SUCCESSOR"
#: The number is the umbrella parent's, accepted (PARENT_ENTITY).
VAT_FROM_PARENT = "VAT_FROM_PARENT"
#: The page names a parent / group, not the record (PARENT_ENTITY_CANDIDATE).
VAT_MAY_BE_PARENTS = "VAT_MAY_BE_PARENTS"
#: The number was read on the operator's site (a clinic's university, …).
VAT_FROM_OPERATOR = "VAT_FROM_OPERATOR"
#: The record names an operator; its site was read, no number for the record.
OPERATED_BY = "OPERATED_BY"
#: Only a parent's / institution's site answered, without a number.
ONLY_PARENT_SITE = "ONLY_PARENT_SITE"
#: The record is a department / branch / sub-unit; the legal entity was looked up.
SUB_UNIT_OF = "SUB_UNIT_OF"
SISTER_ENTITY_ON_PAGE = "SISTER_ENTITY_ON_PAGE"
LEGAL_FORM_DIFFERS = "LEGAL_FORM_DIFFERS"
#: The page gives the number to a company whose name is not the record's.
OTHER_ENTITY_HOLDS_VAT = "OTHER_ENTITY_HOLDS_VAT"
#: Name cores this similar differ only in spelling (no OTHER_ENTITY line).
SPELLING_RATIO = 90
#: A holding / group name: the number is likely the parent's.
_GROUP_RE = re.compile(r"\b(?:holding|holdings|group|gruppe|konzern|beteiligungs\w*)\b", re.I)
#: Another SAP record of the same entity (same name and town) has this number.
SAME_AS_SIBLING = "SAME_AS_SIBLING"
#: Another SAP record of the same entity has a different number.
SIBLING_HAS_OTHER_VAT = "SIBLING_HAS_OTHER_VAT"
#: No number found, but another SAP record of the same entity has one.
SIBLING_HAS_VAT = "SIBLING_HAS_VAT"
NO_VAT_STATED_CODE = "NO_VAT_STATED"
PUBLIC_BODY_NO_VAT_INFERRED = "PUBLIC_BODY_NO_VAT_INFERRED"
SAP_VAT_OUTDATED = "SAP_VAT_OUTDATED"
DELETED_IN_SAP = "DELETED_IN_SAP"
FROM_ARCHIVE_SNAPSHOT = "FROM_ARCHIVE_SNAPSHOT"

#: The defunct statement's keyword → its code, first match wins.
_DEFUNCT_KINDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"verschmolzen|merged\s+(?:into|with)", re.I), MERGED),
    (re.compile(r"umfirmiert", re.I), RENAMED),
    (re.compile(r"in\s+Liquidation|i\.\s?L\.|in\s+Abwicklung|liquidated", re.I), IN_LIQUIDATION),
    (re.compile(r"Insolvenz|insolvent", re.I), INSOLVENT),
    (re.compile(r"aufgelöst|gelöscht|erloschen|dissolved|ceased", re.I), DISSOLVED),
)

_SUB_UNIT_CLASSES = ("SUB_ENTITY", "DEPARTMENT", "BRANCH")

#: Hints from the ranking model's one-line reason per search result (it saw
#: the result's title and snippet, not the page): unverified, labelled so.
PARENT_HINT = "PARENT_HINT"
SUCCESSOR_HINT = "SUCCESSOR_HINT"
DEFUNCT_HINT = "DEFUNCT_HINT"
_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"insolven|liquidat|dissolved|defunct|ceased", re.I), DEFUNCT_HINT),
    (re.compile(r"successor|formerly|renamed|merged|acquired", re.I), SUCCESSOR_HINT),
    (re.compile(r"parent|subsidiar|group company|Tochter", re.I), PARENT_HINT),
)
#: Hints of one kind kept per entity.
HINTS_PER_KIND = 2


def _cell(parts: list[str]) -> str | None:
    text = "\n".join(p for p in parts if p)
    if not text:
        return None
    return text if len(text) <= CELL_CHARS else text[:CELL_CHARS - 1] + "…"


def _quote(text: str | None, n: int = 240) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _hybrid(usage: dict[str, Any]) -> dict[str, Any]:
    """The hybrid lookup's record: the agent's result keeps it under
    ``hybrid_usage`` (searches, ranking and hosts happened there)."""
    return usage.get("hybrid_usage") or usage


def _pages(usage: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every page fetched for the entity, the hybrid lookup's first."""
    return {**(_hybrid(usage).get("pages") or {}), **(usage.get("pages") or {})}


def _defunct_kind(quote: str) -> str:
    for pattern, code in _DEFUNCT_KINDS:
        if pattern.search(quote):
            return code
    return DISSOLVED


def _ranking_hints(hybrid: dict[str, Any]) -> list[str]:
    """``<HINT>: host — reason`` from the ranking model's reasons: a result
    it typed ``parent_site``, or whose reason speaks of a parent, successor
    or insolvency. One per host and kind, :data:`HINTS_PER_KIND` per kind."""
    from vat.web_pages import host_of

    seen: set[tuple[str, str]] = set()
    per_kind: dict[str, int] = {}
    out = []
    for n in (1, 2):
        for item in hybrid.get(f"ranked_{n}") or []:
            reason = str(item.get("reason") or "")
            code = next((c for p, c in _HINTS if p.search(reason)), None)
            if code is None and item.get("type") == "parent_site":
                code = PARENT_HINT
            if code is None:
                continue
            host = host_of(str(item.get("url") or "")) or str(item.get("url") or "")
            if (code, host) in seen or per_kind.get(code, 0) >= HINTS_PER_KIND:
                continue
            seen.add((code, host))
            per_kind[code] = per_kind.get(code, 0) + 1
            out.append(f"{code} (search result, unverified): {host} — {_quote(reason, 160)}")
    return out


def _spelling_only(found: str, names: tuple[str | None, ...]) -> bool:
    """The found name is SAP's but for spelling ("Chemicals" for "Chemical")."""
    if not any(names):
        return False
    from rapidfuzz import fuzz

    from vat.entity_match import name_match, parse_name

    verdict = name_match(found, tuple(names))
    return fuzz.token_sort_ratio(" ".join(verdict.core), " ".join(parse_name(found).core)) >= SPELLING_RATIO


def entity_status(result: FoundResult | None, siblings: list[tuple[str, str]] | None = None,
                  deleted: bool = False, names: tuple[str | None, ...] = ()) -> str | None:
    """What the lookup learned about the entity, one ``CODE: detail`` per
    line: dissolved / merged / renamed (the SAP name or a fetched page says
    so), a parent's, operator's or sibling's number, a sub-unit, a stated
    "no VAT ID"; last, the ranking model's unverified hints. None when
    nothing of the kind was seen. *siblings* are ``(customer, VAT ID)`` of the
    upload's other records of the same entity; *names* the record's Name 1–4."""
    if result is None:
        return None
    from vat.web_pages import _DEFUNCT_RE

    usage = result.usage or {}
    hybrid = _hybrid(usage)
    issues = set(result.all_issues) | set(result.issues)
    out: list[str] = []

    for name in names:
        m = _DEFUNCT_RE.search(name or "")
        if m:
            out.append(f"{_defunct_kind(m.group(0))}: the SAP name says so (\"{name}\")")
            break
    for url, p in _pages(usage).items():
        if p.get("defunct"):
            out.append(f"{_defunct_kind(p['defunct'])}: \"{_quote(p['defunct'])}\" ({url})")
            break
    relation = usage.get("agent_relation")
    if relation in ("renamed", "successor", "parent"):
        code = {"renamed": RENAMED, "successor": SUCCESSOR, "parent": VAT_FROM_PARENT}[relation]
        note = usage.get("agent_notes")
        out.append(f"{code} (agent): {_quote(note) if note else f'the agent judged the relation {relation}'}")
    if "SUCCESSOR_CANDIDATE" in issues:
        out.append(f"{SUCCESSOR}: the number's page names {result.attributed_to or 'another name'}"
                   " — possibly a successor / renamed entity")
    if "PARENT_ENTITY" in issues:
        out.append(f"{VAT_FROM_PARENT}: {result.attributed_to or 'the parent'}")
    elif "PARENT_ENTITY_CANDIDATE" in issues:
        out.append(f"{VAT_MAY_BE_PARENTS}: the page names {result.attributed_to or 'a parent / group'}, "
                   "not the record")
    elif result.value and result.attributed_to and result.name_match != "EXACT" \
            and "SUCCESSOR_CANDIDATE" not in issues and not _spelling_only(result.attributed_to, names):
        group = _GROUP_RE.search(result.attributed_to)
        out.append(f"{VAT_MAY_BE_PARENTS if group else OTHER_ENTITY_HOLDS_VAT}: the page gives the number to "
                   f"{result.attributed_to}{' (a holding / group name)' if group else ''}, not the record's name")
    operator = hybrid.get("operator") or usage.get("operator")
    from_operator = bool(operator and result.value and usage.get("found_via") == "operator")
    if operator:
        if from_operator:
            out.append(f"{VAT_FROM_OPERATOR}: {operator.get('name')} ({operator.get('url')})")
        else:
            out.append(f"{OPERATED_BY}: {operator.get('name')} ({operator.get('url')}; "
                       f"{'read, no number for the record' if operator.get('read') else 'not readable'})")
    if not result.value and (usage.get("not_found_reason") or hybrid.get("not_found_reason")) == "PARENT_ONLY":
        hosts = [h for h, v in (hybrid.get("hosts") or {}).items()
                 if v.get("kind") in ("parent", "institution") and v.get("answered")]
        out.append(f"{ONLY_PARENT_SITE}: {', '.join(hosts) or 'a parent site'} answered, no number for the record")
    elif result.value and not from_operator and usage.get("found_kind") in ("parent", "institution"):
        out.append(f"{VAT_MAY_BE_PARENTS}: read on a {usage['found_kind']} site ({usage.get('source_page')})")
    name_class = str(hybrid.get("name_class") or "")
    unit = next((c for c in _SUB_UNIT_CLASSES if name_class.startswith(c)), None) \
        or next((c for c in _SUB_UNIT_CLASSES if c in issues), None)
    if unit:
        out.append(f"{SUB_UNIT_OF}: {hybrid.get('entity') or result.attributed_to} "
                   f"(the record is a {unit.lower().replace('_', '-')}; the legal entity was looked up)")
    if "SISTER_ENTITY_ON_PAGE" in issues:
        out.append(f"{SISTER_ENTITY_ON_PAGE}: the page also names a sister company")
    for i in issues:
        if i.startswith("LEGAL_FORM_DIFFERS"):
            out.append(f"{LEGAL_FORM_DIFFERS}: SAP/page {i.split(':', 1)[1] if ':' in i else ''}".rstrip()
                       + " — possibly converted")
            break
    if result.status == NO_VAT_STATED:
        no_vat = hybrid.get("no_vat") or {}
        out.append(f"{NO_VAT_STATED_CODE}: \"{_quote(no_vat.get('quote') or result.evidence)}\""
                   f" ({no_vat.get('url') or result.source_url})")
    elif result.status == NO_VAT_INFERRED:
        out.append(f"{PUBLIC_BODY_NO_VAT_INFERRED}: a public body; no page states a number (assumption)")
    if SAP_VAT_OUTDATED in issues:
        out.append(f"{SAP_VAT_OUTDATED}: SAP's number differs from a VIES-valid one the entity publishes")

    if siblings:
        value = re.sub(r"[^0-9A-Z]", "", str(result.value or "").upper())
        same = [c for c, v in siblings if v == value]
        other = [f"{c}={v}" for c, v in siblings if v != value]
        if value and same:
            out.append(f"{SAME_AS_SIBLING}: customer {', '.join(same)}")
        if value and other:
            out.append(f"{SIBLING_HAS_OTHER_VAT}: {', '.join(other)}")
        if not value:
            out.append(f"{SIBLING_HAS_VAT}: {', '.join(other)} (same name and town in SAP)")
    if deleted:
        out.append(f"{DELETED_IN_SAP}: Central Deletion Flag set")
    if "ARCHIVE_SOURCE" in result.flags:
        out.append(f"{FROM_ARCHIVE_SNAPSHOT}: {result.source_url or 'an archived page'}")
    out += _ranking_hints(hybrid)
    return _cell(list(dict.fromkeys(out)))


def name_vs_sap(result: FoundResult | None, names: tuple[str | None, ...] = ()) -> str | None:
    """The name the source gives the number to against SAP's Name 1–4:
    ``<EXACT|PARTIAL|MISMATCH>; SAP: …; found: …; legal form …; only in SAP: …;
    only found: …`` (folded words). None without a found name."""
    if result is None or not result.attributed_to:
        return None
    from vat.entity_match import name_match, parse_name

    sap = " ".join(str(n).strip() for n in names if n and str(n).strip())
    parts = [f"SAP: {sap}" if sap else "SAP: -", f"found: {result.attributed_to}"]
    if not sap:
        return "; ".join(parts)
    verdict = name_match(result.attributed_to, tuple(names))
    found = parse_name(result.attributed_to).core
    if verdict.record_form != verdict.page_form and (verdict.record_form or verdict.page_form):
        parts.append(f"legal form {verdict.record_form or '-'} → {verdict.page_form or '-'}")
    only_sap = [w for w in verdict.core if w not in found]
    only_found = [w for w in found if w not in verdict.core]
    if only_sap:
        parts.append("only in SAP: " + " ".join(only_sap))
    if only_found:
        parts.append("only found: " + " ".join(only_found))
    return "; ".join([verdict.match, *parts])


def evidence_snippet(result: FoundResult | None) -> str | None:
    """Where the number stands: the page text around it as fetched here
    (``usage["evidence_snippet"]``, also when the code picked the number and
    no model quoted it), else the source's own quote, with the URL. A
    snippet counts only if it holds the result's number (the agent may have
    found another than the hybrid lookup)."""
    if result is None or not result.value:
        return None
    usage = result.usage or {}
    digits = re.sub(r"\D", "", result.value)
    snippet = next((s for s in (usage.get("evidence_snippet"), _hybrid(usage).get("evidence_snippet"))
                    if s and digits in re.sub(r"\D", "", s)), None) or result.evidence
    if not snippet:
        return None
    return _quote(snippet, CELL_CHARS - 600) + (f" ({result.source_url})" if result.source_url else "")


def _hosts(hosts: dict[str, dict[str, Any]]) -> list[str]:
    lines = []
    for host, v in hosts.items():
        state = ("blocked " + str(v["blocked"]) if v.get("blocked")
                 else "rejected " + str(v["rejected"]) if v.get("rejected")
                 else "answered" if v.get("answered") else "no Impressum-type page")
        lines.append(f"{host} [{v.get('kind')}, {state}]" + (f" operator {v['operator']}" if v.get("operator") else ""))
    return lines


def _page(url: str, p: dict[str, Any]) -> str:
    bits = [str(p.get("status") or "")]
    if p.get("via"):
        bits.append(f"via {p['via']}")
    if p.get("hits"):
        bits.append("numbers " + ",".join(p["hits"][:4]))
    if p.get("rejected"):
        bits.append(f"rejected {p['rejected']}")
    if p.get("needs_js"):
        bits.append("script-only")
    if p.get("no_vat"):
        bits.append("no-VAT statement")
    if p.get("defunct"):
        bits.append("defunct statement")
    return f"{url} [{'; '.join(b for b in bits if b)}]"


def log_columns(result: FoundResult | None, siblings: list[tuple[str, str]] | None = None,
                deleted: bool = False, sap_vat: str | None = None,
                names: tuple[str | None, ...] = ()) -> dict[str, Any]:
    """The :data:`LOG_COLUMNS` of one row; blank for a row not looked up."""
    if result is None:
        return dict(BLANK_LOG_COLUMNS)
    from vat.no_number_class import classify

    usage = result.usage or {}
    h = _hybrid(usage)
    queries = [f"{label}: {q}" for label, q in (
        ("1", h.get("query_1_run") or h.get("query_1")), ("2", h.get("query_2_run") or h.get("query_2")),
        ("site", (h.get("site_search") or {}).get("query")), ("aggregator", h.get("aggregator_query")),
    ) if q]
    ranking = []
    for n in (1, 2):
        picked = h.get(f"picked_{n}")
        if picked:
            ranking.append(f"{n} ({h.get(f'ranking_{n}') or '-'}): " + ", ".join(f"{u} [{k}]" for u, k in picked))
    if h.get("domain_hint"):
        ranking.append(f"domain hint {h['domain_hint']} ({'passed' if h.get('domain_hint_passed') else 'fetched' if h.get('domain_hint_fetched') else 'not fetched'})")
    name_class = h.get("name_class")
    if h.get("name2_applied"):
        name_class = f"{name_class} (Name 2 classifier: {h.get('name2_verdict')})"
    search = [h.get("search_name") or ""]
    if h.get("entity") and h.get("entity") != h.get("search_name"):
        search.append(f"entity {h['entity']}")
    if h.get("name_used") and h.get("name_used") != h.get("search_name"):
        search.append(f"search 2 used {h['name_used']}")
    if len(h.get("acceptable_names") or []) > 1:
        search.append("accepted " + " / ".join(h["acceptable_names"]))
    operator = h.get("operator") or usage.get("operator")
    peppol = None
    if "peppol_queries" in h or "peppol_entries" in h:
        peppol = f"entries {h.get('peppol_entries', 0)}, {'matched' if h.get('peppol_hit') else 'no match'}"
    aggregator = None
    if h.get("aggregator_query"):
        entries = [e.get("heading") if isinstance(e, dict) else str(e) for e in h.get("aggregator_entries") or []]
        aggregator = f"{len(entries)} entries: " + "; ".join(str(e) for e in entries[:5]) if entries else "no entry"
    af = None
    if not result.value:
        cls, why = classify(h, sap_vat)
        af = f"{cls}: {why}"
    agent = None
    if "agent_relation" in usage or "agent_notes" in usage or "agent_searches" in usage:
        agent = json.dumps({k: usage.get(f"agent_{k}") for k in (
            "relation", "confidence", "value", "source", "notes", "searches", "opens", "pages_opened")
            if usage.get(f"agent_{k}") not in (None, "", [])}, ensure_ascii=False)
    counts = ", ".join(f"{k} {h.get(k)}" for k in ("searches", "fetches", "follows", "probes", "pdf_fetches",
                                                    "archive_fetches", "operator_follows")
                       if h.get(k))
    if usage.get("seconds") or h.get("seconds"):
        counts += f"{', ' if counts else ''}seconds {usage.get('seconds') or h.get('seconds')}"
    if usage is not h and usage.get("fetches"):
        counts += f"; agent fetches {usage.get('fetches')}"
    return {
        "VAT_Found_Entity_Status": entity_status(result, siblings, deleted, names),
        "VAT_Found_Name_vs_SAP": name_vs_sap(result, names),
        "VAT_Found_Evidence_Snippet": evidence_snippet(result),
        "VAT_Log_Name_Class": name_class,
        "VAT_Log_Search_Name": _cell([s for s in search if s]) if any(search) else None,
        "VAT_Log_Queries": _cell(queries),
        "VAT_Log_Ranking": _cell(ranking),
        "VAT_Log_Hosts": _cell(_hosts(h.get("hosts") or {})),
        "VAT_Log_Pages": _cell([_page(u, p) for u, p in _pages(usage).items()]),
        "VAT_Log_Not_Fetched": _cell([f"{n.get('url')} ({n.get('why')})" for n in h.get("not_fetched") or []]),
        "VAT_Log_Dropped": _cell([f"{d.get('vat_id')}: {d.get('why')}" for d in h.get("dropped") or []]),
        "VAT_Log_Operator": (f"{operator.get('name')} ({operator.get('url')}, from {operator.get('from')}; "
                             f"{'read' if operator.get('read') else 'not read'})") if operator else None,
        "VAT_Log_Peppol": peppol,
        "VAT_Log_Aggregator": aggregator,
        "VAT_Log_No_Number_Class": af,
        "VAT_Log_Agent": agent,
        "VAT_Log_Counts": counts or None,
    }
