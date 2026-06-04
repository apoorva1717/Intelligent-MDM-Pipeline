"""Output column mapping for the /enrich/file endpoint.

The result workbook has one column per field returned in the /enrich JSON
response body (i.e. the serialised fields of ``EnrichmentResult``). This
mapping lists every output column in order:

    key   = EnrichmentResult field name (exactly as it appears in the
            response body)
    value = column header written to the XLSX

By default the header is identical to the response field name so the file
matches the response body one-to-one. Edit a value here if you want a
friendlier header in the spreadsheet without changing the API response.
"""

from __future__ import annotations

# Order here defines the column order in the output file.
RESPONSE_COLUMNS: dict[str, str] = {
    "record_id": "record_id",
    "name1_enriched": "Name 1",
    "name2_enriched": "Name 2",
    "name3_enriched": "Name 3",
    "search_term_1": "Search Term 1",
    "search_term_2": "Search Term 2",
    "domain": "Domain",
    "department_domain": "Department Domain",
    "website_url": "Website URL",
    "care_of_enriched": "Care Of",
    "contact_enriched": "Contact",
    "email_enriched": "Email",
    "street_cleaned": "Street 1",
    "street_2_cleaned": "Street 2",
    "street_3_cleaned": "Street 3",
    "suite": "Suite",
    "building": "Building",
    "floor": "Floor",
    "room": "Room",
    "unit": "Unit",
    "mail_stop": "Mail Stop",
    "po_box_extracted": "PO Box",
    "unloading_point": "Unloading Point",
    "mail_code": "Mail Code",
    "flag_for_review": "Flag for Review",
    "flag_reason": "Flag Reason",
    "error": "Error",
    "record_type": "Record Type",
}
