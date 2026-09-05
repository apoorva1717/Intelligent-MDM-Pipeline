"""Row-grain → customer-grain consolidation of company codes and sales orgs.

The SAP customer extract arrives at ROW grain: one row per customer per
company-code / sales-area assignment, so a single customer occupies 1 to 68
rows. Every master-data field is byte-identical across a customer's rows;
only the sales-area fields vary.

The scorer needs two CUSTOMER-level columns the extract does not carry —
``Company_Code_Consolidated`` and ``Sales_Org_Consolidated`` — because
``derived_counts`` (dedup/scoring.py) turns them into ``Company_Code_Count``,
``Sales_Org_Count``, ``score_CompanyCodeCount`` and ``score_CombinedPresence``.
They used to be produced by filtering the extract down to one arbitrary row
per customer, which keeps ONE of a customer's company codes and, when the
surviving row happened to be a sales-area row with a blank company code,
keeps none at all.

This stage is a COLUMN-APPEND, not a collapse: rows in == rows out, in the
same order, with two strings computed per customer and written onto every row
of that customer. Collapsing the extract to one row per customer is a
separate, downstream concern and deliberately not done here.

Permissive in the same way as the scorer (see dedup/scoring.py): a blank
Customer is counted as an error and the row is passed through untouched with
both columns empty — never a raised exception, never a dropped row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field

# The scorer's tolerant header matcher, imported rather than re-spelled: the
# writer here and the reader downstream (dedup/scoring_xlsx.INPUT_HEADERS) must
# never disagree about which spelling of a header is "Company Code".
from dedup.scoring_xlsx import _norm as norm_header

logger = logging.getLogger(__name__)

# The delimiter joining the consolidated values. ONE constant, used by every
# writer here; ``split_consolidated`` in dedup/scoring.py reads both "," and
# ";" so reversing this decision means changing this line and nothing else.
CONSOLIDATED_DELIMITER = ","

# Input columns. Nothing else is read. The snake_case spellings (customer,
# company_code, sales_organization) normalise onto the same keys, so they are
# accepted for free — consistent with populate_by_name elsewhere.
CUSTOMER_HEADER = "Customer"
COMPANY_CODE_HEADER = "Company Code"
SALES_ORG_HEADER = "Sales Organization"

# Output columns. EXACTLY these two: Company_Code_Count / Sales_Org_Count are
# derived by the scorer from these strings, and emitting them here as well
# would create a second source of truth for the same number.
COMPANY_CODE_CONSOLIDATED_HEADER = "Company_Code_Consolidated"
SALES_ORG_CONSOLIDATED_HEADER = "Sales_Org_Consolidated"

INPUT_HEADERS: Tuple[str, ...] = (
    CUSTOMER_HEADER, COMPANY_CODE_HEADER, SALES_ORG_HEADER,
)
OUTPUT_HEADERS: Tuple[str, ...] = (
    COMPANY_CODE_CONSOLIDATED_HEADER, SALES_ORG_CONSOLIDATED_HEADER,
)


# ---------------------------------------------------------------------------
# Cell / key normalisation
# ---------------------------------------------------------------------------

def cell_text(value: object) -> str:
    """A cell as trimmed text; "" for anything blank.

    Excel hands back numeric codes as floats (1140.0), which must render as
    "1140" and not "1140.0" — otherwise the same company code arrives under
    two spellings from two transports and the distinct-value set doubles.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def customer_key(value: object) -> str:
    """Grouping key for one Customer cell: trimmed, leading zeros stripped.

    "0013119338" and "13119338" are one customer. The key is used for
    GROUPING ONLY — the Customer cell itself is never rewritten. "" means the
    row has no usable customer number (an error; see ``Consolidation``).
    """
    text = cell_text(value)
    if not text:
        return ""
    # An all-zero customer number is dirt, but it is still one group rather
    # than an empty key that would collide with the genuinely blank rows.
    return text.lstrip("0") or "0"


def _value_sort_key(value: str) -> Tuple[int, object]:
    """Numeric codes sort numerically; non-numeric sort after them, by text."""
    return (0, int(value)) if value.isdigit() else (1, value)


def consolidate_values(values: Iterable[object]) -> str:
    """Distinct non-blank values, ascending, joined by the delimiter.

    Blanks are DROPPED, never emitted as empty positions: a group of 7 rows
    carrying one sales org yields "2401", not ",,2401,,,,". A value appearing
    on six rows appears once. Same input → byte-identical output.
    """
    distinct: Set[str] = set()
    for value in values:
        text = cell_text(value)
        if text:
            distinct.add(text)
    return CONSOLIDATED_DELIMITER.join(sorted(distinct, key=_value_sort_key))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class ConsolidationSummary(BaseModel):
    """Aggregate statistics for one consolidation run.

    ``rows_in`` and ``rows_out`` are equal by construction — they are both
    reported so a caller can assert the column-append never became a collapse.
    ``errors`` counts rows with a blank or missing Customer; those rows are
    passed through unchanged with both consolidated columns empty.
    """

    rows_in: int = Field(
        default=0, description="Rows received. Equals rows_out, always."
    )
    rows_out: int = Field(
        default=0,
        description=(
            "Rows returned. This is a column-append, not a collapse: assert "
            "rows_out == rows_in to prove no row was dropped or merged."
        ),
    )
    customers: int = Field(
        default=0,
        description=(
            "Distinct customers seen, keyed on Customer with leading zeros "
            "stripped. Rows with a blank Customer are not counted here."
        ),
    )
    customers_with_no_company_code: int = Field(
        default=0,
        description="Customers with no Company Code on ANY of their rows.",
    )
    customers_with_multiple_company_codes: int = Field(
        default=0,
        description=(
            "Customers holding more than one distinct company code — the "
            "population the old one-arbitrary-row-per-customer filter lost."
        ),
    )
    customers_with_no_sales_org: int = Field(
        default=0,
        description="Customers with no Sales Organization on ANY of their rows.",
    )
    customers_with_multiple_sales_orgs: int = Field(
        default=0, description="Customers holding more than one distinct sales org."
    )
    max_rows_per_customer: int = Field(
        default=0,
        description="Largest group size. The real extract runs up to 68.",
    )
    errors: int = Field(
        default=0,
        description=(
            "Rows with a blank or missing Customer. Each was passed through "
            "unchanged with both consolidated columns empty — never dropped."
        ),
    )
    warnings: List[str] = Field(
        default_factory=list,
        description=(
            "Non-fatal notes: a missing Company Code / Sales Organization "
            "input column, blank Customer rows, and the batch-boundary "
            "heuristic (JSON transport only)."
        ),
    )


# ---------------------------------------------------------------------------
# The transform
# ---------------------------------------------------------------------------

@dataclass
class _Group:
    """One customer's accumulated value sets. The two sets are INDEPENDENT —
    they routinely differ in length and nothing is positionally aligned
    between them (the nth company code does not pair with the nth sales org).
    """

    company_codes: Set[str] = field(default_factory=set)
    sales_orgs: Set[str] = field(default_factory=set)
    rows: int = 0


class Consolidation:
    """One pass over the rows, then the two strings for any customer key.

    Shared by both transports so the JSON and XLSX endpoints cannot drift:
    ``observe`` once per input row, ``resolve`` once per output row.
    """

    def __init__(self) -> None:
        self._groups: Dict[str, _Group] = {}
        self._resolved: Dict[str, Tuple[str, str]] = {}
        self.rows_in = 0
        self.errors = 0

    def observe(self, customer: object, company_code: object, sales_org: object) -> str:
        """Fold one input row in. Returns its customer key ("" if unusable)."""
        self.rows_in += 1
        key = customer_key(customer)
        if not key:
            self.errors += 1
            return ""
        group = self._groups.get(key)
        if group is None:
            group = self._groups[key] = _Group()
        group.rows += 1
        text = cell_text(company_code)
        if text:
            group.company_codes.add(text)
        text = cell_text(sales_org)
        if text:
            group.sales_orgs.add(text)
        return key

    def resolve(self, key: str) -> Tuple[str, str]:
        """(company codes, sales orgs) for a key, as delimited strings.

        An unknown key — a row whose Customer was blank — and a customer with
        no company code on any row both get "": an empty string, never None,
        never "None", never "[]".
        """
        if not key:
            return "", ""
        cached = self._resolved.get(key)
        if cached is None:
            group = self._groups.get(key)
            if group is None:
                return "", ""
            cached = self._resolved[key] = (
                consolidate_values(group.company_codes),
                consolidate_values(group.sales_orgs),
            )
        return cached

    def summary(self, *, rows_out: int, warnings: Sequence[str]) -> ConsolidationSummary:
        groups = self._groups.values()
        return ConsolidationSummary(
            rows_in=self.rows_in,
            rows_out=rows_out,
            customers=len(self._groups),
            customers_with_no_company_code=sum(
                1 for g in groups if not g.company_codes
            ),
            customers_with_multiple_company_codes=sum(
                1 for g in groups if len(g.company_codes) > 1
            ),
            customers_with_no_sales_org=sum(1 for g in groups if not g.sales_orgs),
            customers_with_multiple_sales_orgs=sum(
                1 for g in groups if len(g.sales_orgs) > 1
            ),
            max_rows_per_customer=max((g.rows for g in groups), default=0),
            errors=self.errors,
            warnings=list(warnings),
        )


def blank_customer_warning(errors: int) -> List[str]:
    if not errors:
        return []
    return [
        f"{errors} row(s) have a blank Customer — passed through unchanged "
        "with both consolidated columns empty."
    ]


def missing_column_warnings(
    *, has_company_code: bool, has_sales_org: bool
) -> List[str]:
    """A missing INPUT column is a warning, not an error: the corresponding
    consolidated column is written empty for every row."""
    warnings: List[str] = []
    if not has_company_code:
        warnings.append(
            f"No '{COMPANY_CODE_HEADER}' column found — "
            f"{COMPANY_CODE_CONSOLIDATED_HEADER} written empty for every row."
        )
    if not has_sales_org:
        warnings.append(
            f"No '{SALES_ORG_HEADER}' column found — "
            f"{SALES_ORG_CONSOLIDATED_HEADER} written empty for every row."
        )
    return warnings


def batch_boundary_warning(first_key: str, last_key: str) -> List[str]:
    """The §4 heuristic. NOT a guarantee — see ``consolidate_rows``."""
    keys = [k for k in dict.fromkeys([first_key, last_key]) if k]
    if not keys:
        return []
    return [
        "Batch-boundary risk: customer(s) "
        + ", ".join(keys)
        + " sit at the first/last row of this request, which is where a split "
        "would show. Consolidation is only correct when EVERY row of a "
        "customer is in the same request; a customer split across two batches "
        "silently yields two partial lists. Run "
        "POST /api/preprocess/consolidate/file over the complete extract, or "
        "order the ADF split so a customer is never divided."
    ]


# ---------------------------------------------------------------------------
# JSON transport
# ---------------------------------------------------------------------------

def _row_lookup(row: dict) -> Dict[str, str]:
    """Normalised key → the row's own spelling of it (first occurrence wins).

    This is what makes the snake_case aliases work: "company_code",
    "Company Code" and "COMPANY CODE" all normalise to "companycode".
    """
    lookup: Dict[str, str] = {}
    for key in row:
        norm = norm_header(key)
        if norm and norm not in lookup:
            lookup[norm] = key
    return lookup


def _read(row: dict, lookup: Dict[str, str], header: str) -> object:
    key = lookup.get(norm_header(header))
    return row.get(key) if key is not None else None


def _write(row: dict, lookup: Dict[str, str], header: str, value: str) -> None:
    """Write into the row's existing spelling of the column when it has one,
    so a second run overwrites in place instead of appending a near-duplicate
    key — that is what makes re-running idempotent rather than additive."""
    row[lookup.get(norm_header(header), header)] = value


def consolidate_rows(
    rows: Sequence[dict], *, warn_batch_boundary: bool = False
) -> Tuple[List[dict], ConsolidationSummary]:
    """Append the two consolidated columns to every row. Rows in == rows out.

    Rows are shallow-copied, not mutated; original columns, their values and
    their order are untouched. If the two columns are already present they are
    RECOMPUTED AND OVERWRITTEN — a stale consolidation is worse than a
    recomputed one — so a second run over a processed payload is a no-op.

    ``warn_batch_boundary`` emits the §4 heuristic warning. It is a heuristic
    and NOT a guarantee: a single request cannot see the rows that are not in
    it, so it can only name the positions where a split would show. There is
    deliberately no cross-request state — the fix for a split customer is
    ordering in ADF, not memory in this service.
    """
    lookups = [_row_lookup(row) for row in rows]

    has_company_code = False
    has_sales_org = False
    for lookup in lookups:
        has_company_code |= norm_header(COMPANY_CODE_HEADER) in lookup
        has_sales_org |= norm_header(SALES_ORG_HEADER) in lookup

    state = Consolidation()
    keys: List[str] = []
    for row, lookup in zip(rows, lookups):
        keys.append(state.observe(
            _read(row, lookup, CUSTOMER_HEADER),
            _read(row, lookup, COMPANY_CODE_HEADER),
            _read(row, lookup, SALES_ORG_HEADER),
        ))

    out_rows: List[dict] = []
    for row, lookup, key in zip(rows, lookups, keys):
        company_codes, sales_orgs = state.resolve(key)
        out = dict(row)
        _write(out, lookup, COMPANY_CODE_CONSOLIDATED_HEADER, company_codes)
        _write(out, lookup, SALES_ORG_CONSOLIDATED_HEADER, sales_orgs)
        out_rows.append(out)

    warnings = missing_column_warnings(
        has_company_code=has_company_code, has_sales_org=has_sales_org
    ) if rows else []
    warnings += blank_customer_warning(state.errors)
    if warn_batch_boundary and keys:
        warnings += batch_boundary_warning(keys[0], keys[-1])

    return out_rows, state.summary(rows_out=len(out_rows), warnings=warnings)


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------

EXAMPLE_ROWS_IN = [
    {
        "Customer": "0013119338",
        "Name 1": "Contra Costa County",
        "Company Code": "1140",
        "Sales Organization": "",
    },
    {
        "Customer": "13119338",
        "Name 1": "Contra Costa County",
        "Company Code": "1207",
        "Sales Organization": "",
    },
    {
        "Customer": "13119338",
        "Name 1": "Contra Costa County",
        "Company Code": "1240",
        "Sales Organization": "2401",
    },
]

# The same three rows, each carrying the two appended columns. Note what the
# example demonstrates: "0013119338" and "13119338" are ONE customer; the three
# company codes are consolidated onto EVERY row; the two blank sales orgs are
# dropped rather than emitted as empty positions (so the sales-org list has one
# entry, not three); and "Name 1", which this stage never reads, is echoed back
# untouched.
EXAMPLE_ROWS_OUT = [
    {
        **row,
        COMPANY_CODE_CONSOLIDATED_HEADER: "1140,1207,1240",
        SALES_ORG_CONSOLIDATED_HEADER: "2401",
    }
    for row in EXAMPLE_ROWS_IN
]

EXAMPLE_SUMMARY = {
    "rows_in": 3,
    "rows_out": 3,
    "customers": 1,
    "customers_with_no_company_code": 0,
    "customers_with_multiple_company_codes": 1,
    "customers_with_no_sales_org": 0,
    "customers_with_multiple_sales_orgs": 0,
    "max_rows_per_customer": 3,
    "errors": 0,
    "warnings": [
        "Batch-boundary risk: customer(s) 13119338 sit at the first/last row "
        "of this request, ..."
    ],
}


EXAMPLE_REQUEST = {"rows": EXAMPLE_ROWS_IN}
EXAMPLE_RESPONSE = {"rows": EXAMPLE_ROWS_OUT, "summary": EXAMPLE_SUMMARY}


class ConsolidateRequest(BaseModel):
    """POST /api/preprocess/consolidate request body.

    Rows are free-form dicts, not a typed model: every original column is
    echoed back untouched, and only ``Customer``, ``Company Code`` and
    ``Sales Organization`` are read. An empty rows list is valid and returns
    a zeroed summary.
    """

    model_config = ConfigDict(
        json_schema_extra={"examples": [EXAMPLE_REQUEST]}
    )

    rows: List[dict] = Field(
        default_factory=list,
        description=(
            "The row-grain extract rows, one JSON object per row, in file "
            "order. Keys are the extract's own column headers — pass the "
            "WHOLE row: every key is echoed back untouched and only "
            f"'{CUSTOMER_HEADER}', '{COMPANY_CODE_HEADER}' and "
            f"'{SALES_ORG_HEADER}' are read (the snake_case spellings "
            "'customer', 'company_code', 'sales_organization' are also "
            "accepted). Header matching ignores case, spacing and "
            "punctuation. Send every row of a customer in the SAME request — "
            "see the endpoint description."
        ),
    )


class ConsolidateResponse(BaseModel):
    """The same rows, in the same order, plus the two columns and a summary."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [EXAMPLE_RESPONSE]
        }
    )

    rows: List[dict] = Field(
        default_factory=list,
        description=(
            "The request's rows, same count and same order, each with "
            f"'{COMPANY_CODE_CONSOLIDATED_HEADER}' and "
            f"'{SALES_ORG_CONSOLIDATED_HEADER}' added. Every other key is "
            "the caller's own, unmodified."
        ),
    )
    summary: ConsolidationSummary = Field(default_factory=ConsolidationSummary)
