"""Rows in the SAP export format → the same rows with VAT result columns.

Shared by ``POST /api/validate/vat`` and ``tools/vat_validate_xlsx.py``.

**Customer-level fan-out.** The extract has one row per company code / sales
organisation, but the VAT ID (KNA1-STCEG) belongs to the customer. Every row
is format-checked; VIES is asked once per unique normalised VAT across the
whole batch, and that one answer is written to every row carrying it — which
is every row of a consistent customer.

**A conflict is flagged, never resolved.** When one Customer's rows do not all
carry the same normalised VAT (a typo on one row, a blank on another), every
row of that customer is flagged ``CUSTOMER_VAT_CONFLICT`` and each row keeps
the result for its own value. No row's value is preferred over another's.

The Customer value is grouped on exactly as read (whitespace-trimmed, as the
upload reader trims every cell) — no zero-stripping, no padding.
"""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any, Iterable, Mapping

from pydantic import AliasChoices

from api.models import EnrichmentRecord
from api.routes import _norm_header
from vat.lookup import AGREEMENT_COLUMN, BLANK_COLUMNS, agreement, grounded_compare_columns
from vat.lookup import (
    COMPARE_COLUMNS, FOUND_VS_SAP, GROUNDED_VS_SAP, LOOKUP_COLUMNS, SCOPE_ALL, SCOPE_MISSING,
    vs_sap,
)
from vat.costs import COST_COLUMNS, cost_columns
from vat.entity_match import NAME_EXACT, SAP_VAT_OUTDATED, TRUSTED_CLASSES
from vat.lookup import DIFFERS_FROM_SAP, FoundResult
from vat.lookup import LookupEngine, LookupInput, in_scope, lookup_kpis, run_lookups
from vat.name_match import NameMatch, match_name
from vat.normalize import VatFormat, assess_format
#: VAT_VIES_Status for a row VIES was not asked about (not FORMAT_OK, or VIES
#: switched off). Defined in vies_client; re-exported here for its importers.
from vat.vies_client import ERROR, NOT_CHECKED, SERVICE_UNAVAILABLE, VIESClient, ViesResult

#: Output columns, appended after every input column, in this order.
VAT_COLUMNS: list[str] = [
    "VAT_Normalized",
    "VAT_Format_Status",
    "VAT_VIES_Status",
    "VAT_VIES_Name",
    "VAT_VIES_Address",
    "VAT_Name_Disclosed",
    "VAT_Name_Match",
    "VAT_Request_Date",
    "VAT_Consultation_No",
    "VAT_Flags",
]

#: One Customer, more than one VAT value across its rows.
CUSTOMER_VAT_CONFLICT = "CUSTOMER_VAT_CONFLICT"
#: The "; " join used for Flag Codes elsewhere in the pipeline.
FLAG_SEPARATOR = "; "

#: Input roles → the EnrichmentRecord field whose accepted headers they reuse.
_ROLE_FIELDS = {
    "customer": "customer",
    "country": "country_region_key",
    "vat": "vat_registration_no",
    "name_1": "name_1",
    "name_2": "name_2",
    "name_3": "name_3",
    "name_4": "name_4",
}
#: Read only when a missing-VAT lookup runs (vat/lookup.py), so the summary's
#: "columns" — and so the default output — is unchanged without one.
_LOOKUP_ROLE_FIELDS = {
    "street": "street_1",
    "house_number": "house_number",
    "postal_code": "postal_code",
    "city": "city",
    # Optional inputs of the design paper (§2). Not SAP export columns: read
    # when a workbook carries them, ignored otherwise.
    "website": "website",
    "email": "email",
    "register_number": "register_number",
    # SAP's Central Deletion Flag: an archive snapshot may be a source.
    "deleted": "central_deletion_flag",
}
#: Headers EnrichmentRecord does not accept: the SAP export's first name
#: column is a bare "Name", not "Name 1"; website and register number are
#: not EnrichmentRecord fields at all.
_EXTRA_HEADERS = {
    "name_1": ("Name",),
    "website": ("Website", "Web Site", "Homepage", "URL", "Domain", "Email Domain"),
    "register_number": (
        "Commercial Register Number", "Commercial Register No.", "Register Number",
        "Handelsregisternummer", "Handelsregister", "Registernummer", "HRB",
    ),
}


def _accepted_headers(field: str) -> set[str]:
    names: list[str] = []
    if field in EnrichmentRecord.model_fields:
        alias = EnrichmentRecord.model_fields[field].validation_alias
        names = list(alias.choices) if isinstance(alias, AliasChoices) else [alias or field]
    names += _EXTRA_HEADERS.get(field, ())
    return {_norm_header(n) for n in [*names, field]}


def resolve_columns(
    headers: Iterable[str], roles: Mapping[str, str] = _ROLE_FIELDS,
) -> dict[str, str | None]:
    """The input header that carries each role, matched the way the upload
    reader matches (case, spacing and punctuation ignored). First match wins."""
    found: dict[str, str | None] = {role: None for role in roles}
    accepted = {role: _accepted_headers(f) for role, f in roles.items()}
    for header in headers:
        norm = _norm_header(header)
        for role, names in accepted.items():
            if found[role] is None and norm in names:
                found[role] = header
    return found


def _cell(row: Mapping[str, Any], header: str | None) -> str | None:
    if header is None:
        return None
    value = row.get(header)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _flag_set(value: str | None) -> bool:
    """SAP's ``X`` (and the usual spellings of true)."""
    return (value or "").strip().lower() in ("x", "true", "1", "yes", "ja")


def _sap_outdated(result: FoundResult | None, sap_normalized: str | None, country: str | None):
    """The result, with ``SAP_VAT_OUTDATED`` when SAP's VAT ID is not the one
    found: a different, VIES-valid number from a class A / B source naming
    the exact entity. Only scope ``all`` compares with SAP."""
    if (result is None or vs_sap(result.value, sap_normalized, country) != DIFFERS_FROM_SAP
            or result.validation_status != "VALID" or result.name_match != NAME_EXACT
            or result.source_class not in TRUSTED_CLASSES):
        return result
    return replace(result, issues=(*result.issues, SAP_VAT_OUTDATED))


def _conflicting_customers(
    customers: list[str | None], formats: list[VatFormat],
) -> set[str]:
    values: dict[str, set[str | None]] = defaultdict(set)
    for customer, fmt in zip(customers, formats):
        if customer is not None:
            values[customer].add(fmt.normalized)
    return {c for c, seen in values.items() if len(seen) > 1}


def _flags(
    fmt: VatFormat, vies: ViesResult | None, name: NameMatch, conflict: bool,
) -> str | None:
    flags = list(fmt.flags)
    if fmt.format_error:
        flags.append(f"FORMAT_ERROR:{fmt.format_error}")
    if vies is not None and vies.error_code:
        flags.append(f"VIES:{vies.error_code}")
    flags.append(name.flag)
    if conflict:
        flags.append(CUSTOMER_VAT_CONFLICT)
    return FLAG_SEPARATOR.join(flags) or None


def _result_columns(
    fmt: VatFormat, vies: ViesResult | None, names: list[str | None], conflict: bool,
) -> dict[str, Any]:
    vies_status = vies.status if vies else NOT_CHECKED
    name = match_name(vies_status, vies.name if vies else None, names[0], names[1:])
    return {
        "VAT_Normalized": fmt.normalized,
        "VAT_Format_Status": fmt.status,
        "VAT_VIES_Status": vies_status,
        "VAT_VIES_Name": vies.name if vies else None,
        "VAT_VIES_Address": vies.address if vies else None,
        "VAT_Name_Disclosed": vies.name_disclosed if vies else None,
        "VAT_Name_Match": name.status,
        "VAT_Request_Date": vies.request_date if vies else None,
        "VAT_Consultation_No": vies.consultation_number if vies else None,
        "VAT_Flags": _flags(fmt, vies, name, conflict),
    }


async def validate_rows(
    rows: list[Mapping[str, Any]], client: VIESClient,
    lookup: LookupEngine | None = None,
    compare_lookup: LookupEngine | None = None,
    scope: str = SCOPE_MISSING,
    agent: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every row, unchanged, with :data:`VAT_COLUMNS` appended; plus a summary.

    With a *lookup* engine (``vat/lookup.py``), the :data:`LOOKUP_COLUMNS` are
    appended too, filled for in-scope rows (VAT blank, key DE) and blank for
    the rest. Without one the output is exactly as before.

    With a *compare_lookup* as well (the grounded lookup next to the LLM-only
    experiment), the same in-scope rows are looked up a second time and
    :data:`vat.lookup.COMPARE_COLUMNS` are appended: that engine's result and
    ``VAT_LLM_vs_Grounded``.

    With an *agent* (``vat.lookup_agent.make_agent``), the agentic pass of
    Step AG runs after the lookup on every row it left NOT_FOUND or
    LOOKUP_INCOMPLETE (never NOT_FOUND_NO_VAT_EXPECTED), with the lookup's
    result and the VAT IDs on the upload's other records of the same entity
    as context; a number it finds replaces the row's result, gated like the
    lookup's. ``summary.lookup.agent_pass`` reports it.

    *scope* ``all`` (evaluation) looks up every DE row, VAT ID or not, and adds
    ``VAT_Found_vs_SAP`` (and ``VAT_Grounded_vs_SAP``): each proposal against
    the SAP value. The lookups never see that value — :class:`LookupInput`
    carries name and address only.

    Raises ``ValueError`` when no row has a VAT Registration No. column.
    """
    headers: dict[str, None] = {}
    for row in rows:
        headers.update(dict.fromkeys(row))
    cols = resolve_columns(headers)
    if cols["vat"] is None:
        raise ValueError("No 'VAT Registration No.' column in the input rows.")

    customers = [_cell(r, cols["customer"]) for r in rows]
    formats = [assess_format(_cell(r, cols["vat"]), _cell(r, cols["country"])) for r in rows]
    conflicts = _conflicting_customers(customers, formats)
    vies = await client.check_many(formats)

    out: list[dict[str, Any]] = []
    for row, customer, fmt in zip(rows, customers, formats):
        result = vies.get(fmt.normalized) if fmt.vies_eligible else None
        names = [_cell(row, cols[f"name_{n}"]) for n in range(1, 5)]
        out.append({**row, **_result_columns(fmt, result, names, customer in conflicts)})
    summary = summarize(out, customers, cols, vies)

    if lookup is not None:
        lcols = resolve_columns(headers, _LOOKUP_ROLE_FIELDS)
        inputs = [
            LookupInput(
                customer=customer,
                names=tuple(_cell(row, cols[f"name_{n}"]) for n in range(1, 5)),
                street=_cell(row, lcols["street"]),
                house_number=_cell(row, lcols["house_number"]),
                postal_code=_cell(row, lcols["postal_code"]),
                city=_cell(row, lcols["city"]),
                country=fmt.country_key,
                website=_cell(row, lcols["website"]),
                email=_cell(row, lcols["email"]),
                register_number=_cell(row, lcols["register_number"]),
                deleted=_flag_set(_cell(row, lcols["deleted"])),
            ) if in_scope(fmt, scope) else None
            for row, customer, fmt in zip(rows, customers, formats)
        ]
        if compare_lookup is not None:
            # Independent of each other: run side by side, not one after the other.
            found, second = await asyncio.gather(
                run_lookups(lookup, inputs), run_lookups(compare_lookup, inputs),
            )
        else:
            found, second = await run_lookups(lookup, inputs), None
        agent_summary = None
        if agent is not None:
            from vat.lookup_agent import agent_pass
            found, agent_summary = await agent_pass(
                agent, inputs, found, sap=[f.normalized if scope == SCOPE_ALL else None for f in formats],
                siblings=_sibling_vat_ids(rows, cols, lcols, customers, formats, inputs))
        if scope == SCOPE_ALL:
            found = [_sap_outdated(r, f.normalized, f.country_key) for r, f in zip(found, formats)]
            if second is not None:
                second = [_sap_outdated(r, f.normalized, f.country_key)
                          for r, f in zip(second, formats)]
        queued = 0
        if client.enabled and not client.frozen:
            from vat.vies_retry import ViesRetryQueue, enqueue
            queue = ViesRetryQueue.for_client(client)
            queued = enqueue(queue, [*found, *(second or [])])
            if queued:
                queue.save()
        priced = any(r is not None and "costs" in (r.usage or {}) for r in found)
        for row, result in zip(out, found):
            row.update(result.columns() if result else BLANK_COLUMNS)
            if priced:  # the engine reports cost lines (llm_web): every row gets the columns
                row.update(cost_columns((result.usage or {}).get("costs") if result else None))
        if scope == SCOPE_ALL:
            for row, inp, fmt, a in zip(out, inputs, formats, found):
                row[FOUND_VS_SAP] = (
                    vs_sap(a.value if a else None, fmt.normalized, inp.country)
                    if inp is not None else None
                )
        summary["lookup"] = {
            "mode": lookup.mode,
            "scope": scope,
            "rows_in_scope": sum(f is not None for f in found),
            "status": dict(Counter(r["VAT_Found_Status"] for r in out if r["VAT_Found_Status"])),
            "kpis": lookup_kpis(found),
            "cost": lookup.stats(),
            "vies_retry_queued": queued,
        }
        if agent_summary is not None:
            summary["lookup"]["agent_pass"] = agent_summary
            summary["lookup"]["agent_cost"] = agent.stats()
        if scope == SCOPE_ALL:
            summary["lookup"]["vs_sap"] = dict(Counter(r[FOUND_VS_SAP] for r in out if r[FOUND_VS_SAP]))
        if compare_lookup is not None:
            for row, inp, fmt, a, b in zip(out, inputs, formats, found, second):
                row.update(grounded_compare_columns(b))
                row[AGREEMENT_COLUMN] = (
                    agreement(a.value if a else None, b.value if b else None, inp.country)
                    if inp is not None else None
                )
                if scope == SCOPE_ALL:
                    row[GROUNDED_VS_SAP] = (
                        vs_sap(b.value if b else None, fmt.normalized, inp.country)
                        if inp is not None else None
                    )
            summary["compare_lookup"] = {
                "mode": compare_lookup.mode,
                "status": dict(Counter(r["VAT_Grounded_Status"] for r in out if r["VAT_Grounded_Status"])),
                "agreement": dict(Counter(r[AGREEMENT_COLUMN] for r in out if r[AGREEMENT_COLUMN])),
                "kpis": lookup_kpis(second),
                "cost": compare_lookup.stats(),
            }
            if scope == SCOPE_ALL:
                summary["compare_lookup"]["vs_sap"] = dict(
                    Counter(r[GROUNDED_VS_SAP] for r in out if r[GROUNDED_VS_SAP]))
    # After the lookup: its writes go through the same cache and counter.
    summary["cache_write_failures"] = client.write_failures.count
    return out, summary


def _sibling_vat_ids(rows, cols, lcols, customers, formats, inputs) -> dict[str, list[str]]:
    """``customer → VAT IDs`` (FORMAT_OK, normalised) on the upload's other
    records of the same entity key, for the looked-up customers (Step AG's
    context; the record's own number is never in it)."""
    from vat.lookup import entity_key

    wanted = {str(i.customer): entity_key(i) for i in inputs if i is not None}
    if not wanted:
        return {}
    by_key: dict[str, set[tuple[str, str]]] = {}
    for row, customer, fmt in zip(rows, customers, formats):
        if not (fmt.vies_eligible and fmt.normalized):
            continue
        key = entity_key(LookupInput(
            customer=customer, names=tuple(_cell(row, cols[f"name_{n}"]) for n in range(1, 5)),
            street=None, house_number=None, postal_code=None,
            city=_cell(row, lcols["city"]), country=fmt.country_key))
        if key:
            by_key.setdefault(key, set()).add((str(customer), fmt.normalized))
    return {c: sorted({v for o, v in by_key.get(k, ()) if o != c}) for c, k in wanted.items() if k}


#: Every result column, in output order. Which of them a result carries
#: depends on the run (lookup mode, side-by-side, scope).
ALL_RESULT_COLUMNS: list[str] = (
    VAT_COLUMNS + LOOKUP_COLUMNS + list(COST_COLUMNS.values()) + [FOUND_VS_SAP] + COMPARE_COLUMNS
    + [GROUNDED_VS_SAP]
)


def result_columns(out: list[dict[str, Any]]) -> list[str]:
    """The result columns these rows carry, in output order."""
    present = set(out[0]) if out else set()
    return [c for c in ALL_RESULT_COLUMNS if c in present]


def summarize(
    out: list[dict[str, Any]],
    customers: list[str | None],
    cols: dict[str, str | None],
    vies: Mapping[str, ViesResult],
) -> dict[str, Any]:
    """Counts for the response body and the runner's Summary sheet."""
    by_country: dict[str, dict[str, Counter]] = defaultdict(
        lambda: {"format_status": Counter(), "vies_status": Counter()},
    )
    for row in out:
        country = _cell(row, cols["country"]) or "(blank)"
        by_country[country]["format_status"][row["VAT_Format_Status"]] += 1
        by_country[country]["vies_status"][row["VAT_VIES_Status"]] += 1
    # Numbers VIES did not answer. Neither state is cached, so a re-run asks
    # VIES again for exactly these and replays every VALID / INVALID.
    rows_per_vat = Counter(r["VAT_Normalized"] for r in out if r["VAT_VIES_Status"] != NOT_CHECKED)
    unanswered = [
        {"vat": vat, "status": r.status, "error_code": r.error_code, "rows": rows_per_vat[vat]}
        for vat, r in sorted(vies.items())
        if r.status in (SERVICE_UNAVAILABLE, ERROR)
    ]
    return {
        "rows": len(out),
        "unique_customers": len({c for c in customers if c is not None}),
        "rows_without_customer": sum(c is None for c in customers),
        "unique_vies_lookups": len(vies),
        "vies_network_calls": sum(r.attempts for r in vies.values()),
        "vies_from_cache": sum(r.from_cache for r in vies.values()),
        "vies_service_unavailable_vat_ids": sum(
            u["status"] == SERVICE_UNAVAILABLE for u in unanswered
        ),
        "vies_service_unavailable_rows": sum(
            u["rows"] for u in unanswered if u["status"] == SERVICE_UNAVAILABLE
        ),
        "vies_unanswered": unanswered,
        "customers_with_vat_conflict": len({
            c for c, row in zip(customers, out)
            if row["VAT_Flags"] and CUSTOMER_VAT_CONFLICT in row["VAT_Flags"]
        }),
        "format_status": dict(Counter(r["VAT_Format_Status"] for r in out)),
        "vies_status": dict(Counter(r["VAT_VIES_Status"] for r in out)),
        "name_match": dict(Counter(r["VAT_Name_Match"] for r in out)),
        "by_country": {
            k: {kind: dict(c) for kind, c in v.items()}
            for k, v in sorted(by_country.items())
        },
        "columns": cols,
    }
