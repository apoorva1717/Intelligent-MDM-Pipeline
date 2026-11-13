"""Review groups (2026-11-12): which Review rows are likely right, doubtful or likely wrong.

A Review row is put in one of four groups from its reviewer columns
(Name_Check, Address_Check, Issue_Flags, VAT_Found_Source_Class), so a
reviewer can sample groups 1, 2 and 4 and fully review only group 3:

1. likely right, own source — the company's own legal notice or Peppol
   (class A), the same or a similar name (similar only at SAP's address or
   in SAP's town), no warning flag;
2. likely right, directory only — a directory entry (not class A) with the
   same name (or SAP's name plus more), no warning flag;
3. doubtful — everything else: a soft flag (legal form differs, renamed,
   name shared by others, old archive), SAP's name plus more words in another
   town, a similar name from a directory;
4. likely wrong — the evidence points to another company: a different name,
   a similar name in another town, or a parent company / several numbers on
   the page / a sister company / in liquidation.

A Not found row is "no VAT expected" when it is a private person
(Customer_Type PERSON, not a sole trader), a public body without a number,
a closed company or a page that says there is no VAT number; every other
Not found row is "search by hand".

Nothing is looked up; the groups are not decisions.
"""

from __future__ import annotations

from typing import Any

RIGHT_OWN = "1 likely right (own source)"
RIGHT_DIRECTORY = "2 likely right (directory)"
DOUBTFUL = "3 doubtful"
WRONG = "4 likely wrong"
GROUPS = (RIGHT_OWN, RIGHT_DIRECTORY, DOUBTFUL, WRONG)
NO_VAT_PERSON = "no VAT expected (private person)"
SEARCH = "search by hand"
#: Not_Found_Reason values for which no number is expected.
NO_VAT_REASONS = {"PUBLIC_BODY_NO_VAT": "no VAT expected (public body)",
                  "PAGE_SAYS_COMPANY_CLOSED": "no VAT expected (company closed)",
                  "PAGE_SAYS_NO_VAT_ID": "no VAT expected (page says no VAT number)"}

#: Flags that point to another company.
OTHER_COMPANY = frozenset({"MAYBE_PARENT_COMPANY", "SEVERAL_NUMBERS_ON_PAGE", "SISTER_COMPANY_ON_PAGE",
                           "IN_LIQUIDATION"})
#: Flags that make a row doubtful.
SOFT = frozenset({"NAME_SHARED_BY_OTHERS", "LEGAL_FORM_DIFFERS", "MAYBE_RENAMED_OR_SUCCESSOR", "FROM_OLD_ARCHIVE"})
SAME = ("SAME_NAME", "SAP_NAME_PLUS_MORE")
NEAR = ("SAME_ADDRESS", "OTHER_STREET_SAME_TOWN")

_NAME = {"SAME_NAME": "same name", "SAP_NAME_PLUS_MORE": "SAP name plus more words",
         "SIMILAR_NAME": "similar name", "DIFFERENT_NAME": "different name"}
_ADDRESS = {"SAME_ADDRESS": "same address", "OTHER_STREET_SAME_TOWN": "same town, other street",
            "OTHER_TOWN": "other town", "NO_ADDRESS_ON_PAGE": "no address on the page"}


def flags_of(row: dict[str, Any]) -> set[str]:
    return {f.split(":")[0].strip() for f in str(row.get("Issue_Flags") or "").split(";") if f.strip()}


def group_of(row: dict[str, Any]) -> tuple[str, str]:
    """``(group, why)`` of one Review row."""
    name, address = row.get("Name_Check"), row.get("Address_Check")
    own = row.get("VAT_Found_Source_Class") == "A"
    flags = flags_of(row)
    why = "; ".join(x for x in (
        "own legal notice / Peppol" if own else "directory only",
        _NAME.get(name, name or "no name check"), _ADDRESS.get(address, address or "no address check"),
        ", ".join(sorted(flags & (OTHER_COMPANY | SOFT))) or None) if x)
    if name == "DIFFERENT_NAME" or (name == "SIMILAR_NAME" and address == "OTHER_TOWN") or flags & OTHER_COMPANY:
        return WRONG, why
    if flags & SOFT:
        return DOUBTFUL, why
    if own and ((name == "SAME_NAME") or (name in SAME and address in NEAR)
                or (name == "SIMILAR_NAME" and address in NEAR)):
        return RIGHT_OWN, why
    if not own and name in SAME:
        return RIGHT_DIRECTORY, why
    return DOUBTFUL, why


def not_found_group(row: dict[str, Any]) -> str:
    """Group of one Not found row: "search by hand" only when a VAT number is really expected."""
    if row.get("Customer_Type") == "PERSON":
        return NO_VAT_PERSON
    return NO_VAT_REASONS.get(row.get("Not_Found_Reason"), SEARCH)
