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

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

from pydantic import AliasChoices

from api.models import EnrichmentRecord
from api.routes import _norm_header
from vat.lookup import BLANK_COLUMNS, LookupEngine, LookupInput, in_scope, run_lookups
from vat.name_match import NameMatch, match_name
from vat.normalize import VatFormat, assess_format
from vat.vies_client import ERROR, SERVICE_UNAVAILABLE, VIESClient, ViesResult

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

#: VAT_VIES_Status for a row VIES was not asked about (not FORMAT_OK).
NOT_CHECKED = "NOT_CHECKED"
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
}
#: Headers the SAP export uses that EnrichmentRecord does not accept: the
#: export's first name column is a bare "Name", not "Name 1".
_EXTRA_HEADERS = {"name_1": ("Name",)}


def _accepted_headers(field: str) -> set[str]:
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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every row, unchanged, with :data:`VAT_COLUMNS` appended; plus a summary.

    With a *lookup* engine (``vat/lookup.py``), the :data:`LOOKUP_COLUMNS` are
    appended too, filled for in-scope rows (VAT blank, key DE) and blank for
    the rest. Without one the output is exactly as before.

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
            ) if in_scope(fmt) else None
            for row, customer, fmt in zip(rows, customers, formats)
        ]
        found = await run_lookups(lookup, inputs)
        for row, result in zip(out, found):
            row.update(result.columns() if result else BLANK_COLUMNS)
        summary["lookup"] = {
            "mode": lookup.mode,
            "rows_in_scope": sum(f is not None for f in found),
            "status": dict(Counter(r["VAT_Found_Status"] for r in out if r["VAT_Found_Status"])),
            "cost": lookup.stats(),
        }
    # After the lookup: its writes go through the same cache and counter.
    summary["cache_write_failures"] = client.write_failures.count
    return out, summary


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
