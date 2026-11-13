"""Review groups (vat/review_groups.py): each group's rule and its reason text."""

import pytest

from vat.review_groups import (DOUBTFUL, NO_VAT_PERSON, RIGHT_DIRECTORY, RIGHT_OWN, SEARCH, WRONG, group_of,
                               not_found_group)


def row(name="SAME_NAME", address="SAME_ADDRESS", cls="A", flags=None):
    return {"Name_Check": name, "Address_Check": address, "VAT_Found_Source_Class": cls, "Issue_Flags": flags}


@pytest.mark.parametrize("r, group", [
    # Own source, the same company.
    (row(), RIGHT_OWN),
    (row(address="OTHER_TOWN"), RIGHT_OWN),                      # a head office
    (row(address="NO_ADDRESS_ON_PAGE"), RIGHT_OWN),
    (row("SIMILAR_NAME", "OTHER_STREET_SAME_TOWN"), RIGHT_OWN),
    (row("SAP_NAME_PLUS_MORE", "SAME_ADDRESS"), RIGHT_OWN),
    (row(flags="NUMBER_NOT_QUOTED; DEPARTMENT_OR_UNIT"), RIGHT_OWN),
    # A directory with the same name.
    (row(cls="C"), RIGHT_DIRECTORY),
    (row(cls="C", address="OTHER_TOWN", flags="FROM_DIRECTORY"), RIGHT_DIRECTORY),
    (row("SAP_NAME_PLUS_MORE", "OTHER_TOWN", cls=None), RIGHT_DIRECTORY),
    # Doubtful.
    (row(flags="LEGAL_FORM_DIFFERS"), DOUBTFUL),
    (row(flags="MAYBE_RENAMED_OR_SUCCESSOR:x"), DOUBTFUL),
    (row("SAP_NAME_PLUS_MORE", "OTHER_TOWN"), DOUBTFUL),
    (row("SIMILAR_NAME", "SAME_ADDRESS", cls="C"), DOUBTFUL),
    # Another company.
    (row("DIFFERENT_NAME"), WRONG),
    (row("SIMILAR_NAME", "OTHER_TOWN"), WRONG),
    (row(flags="MAYBE_PARENT_COMPANY"), WRONG),
    (row(flags="SEVERAL_NUMBERS_ON_PAGE; NUMBER_NOT_QUOTED"), WRONG),
])
def test_groups(r, group):
    assert group_of(r)[0] == group


def test_why_names_source_checks_and_flags():
    why = group_of(row("SIMILAR_NAME", "OTHER_TOWN", cls="C", flags="LEGAL_FORM_DIFFERS; FROM_DIRECTORY"))[1]
    assert why == "directory only; similar name; other town; LEGAL_FORM_DIFFERS"


@pytest.mark.parametrize("customer_type, reason, group", [
    ("PERSON", "NO_WEBSITE_FOUND", NO_VAT_PERSON),
    ("PERSON_BUSINESS", "NO_WEBSITE_FOUND", SEARCH),      # a sole trader has a VAT number
    ("ORGANISATION", "WEBSITE_HAS_NO_VAT_ID", SEARCH),
    (None, None, SEARCH),
    ("ORGANISATION", "PUBLIC_BODY_NO_VAT", "no VAT expected (public body)"),
    (None, "PAGE_SAYS_COMPANY_CLOSED", "no VAT expected (company closed)"),
    ("PERSON_BUSINESS", "PAGE_SAYS_NO_VAT_ID", "no VAT expected (page says no VAT number)"),
])
def test_not_found_group(customer_type, reason, group):
    assert not_found_group({"Customer_Type": customer_type, "Not_Found_Reason": reason}) == group
