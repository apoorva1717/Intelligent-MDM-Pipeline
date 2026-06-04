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
    "name2_enriched": "name2_enriched",
    "name3_enriched": "name3_enriched",
    "search_term_1": "search_term_1",
    "search_term_2": "search_term_2",
    "department_domain": "department_domain",
    "care_of_enriched": "care_of_enriched",
    "contact_enriched": "contact_enriched",
    "email_enriched": "email_enriched",
    "street_cleaned": "street_cleaned",
    "street_2_cleaned": "street_2_cleaned",
    "street_3_cleaned": "street_3_cleaned",
    "suite": "suite",
    "building": "building",
    "floor": "floor",
    "room": "room",
    "unit": "unit",
    "mail_stop": "mail_stop",
    "po_box_extracted": "po_box_extracted",
    "unloading_point": "unloading_point",
    "mail_code": "mail_code",
    "record_type": "record_type",
    "domain": "domain",
    "website_url": "website_url",
    "flag_for_review": "flag_for_review",
    "flag_reason": "flag_reason",
    "error": "error",
}
