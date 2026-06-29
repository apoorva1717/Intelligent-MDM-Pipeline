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

# Order here defines the column order in the output file. It mirrors the
# original SAP input layout — every input column is carried through (the
# enriched/cleaned value where enrichment applies, the verbatim value
# otherwise) — with enrichment-derived columns inserted next to the field
# they relate to and the review metadata appended at the end.
RESPONSE_COLUMNS: dict[str, str] = {
    # ── Identity & administrative ────────────────────────────────────
    "record_id": "Customer",
    "ecc_customer_number": "ECC Customer Number",
    "central_deletion_flag": "Central Deletion Flag",
    "comments": "Comments",
    "account_group": "Account group",
    "company_code": "Company Code",
    "sales_organization": "Sales Organization",
    "distribution_channel": "Distribution Channel",
    "division": "Division",
    # ── Name block (enriched) + web enrichment ───────────────────────
    "name1_enriched": "Name 1",
    "name2_enriched": "Name 2",
    "name3_enriched": "Name 3",
    "name4_enriched": "Name 4",
    # "Domain" and "Website URL" merged into a single "Domain" column that
    # carries the website URL value; the separate "Website URL" column and the
    # bare-domain value are no longer emitted.
    "website_url": "Domain",
    "department_domain": "Department Domain",
    # ── Contact block (enriched) ─────────────────────────────────────
    "care_of_enriched": "Care Of",
    "contact_enriched": "Contact",
    "email_enriched": "Email",
    # ── Address block (cleaned) + extracted sub-locations ────────────
    "street_cleaned": "Street 1",
    "house_number": "House Number",
    "street_2_cleaned": "Street 2",
    "street_3_cleaned": "Street 3",
    "street_4_cleaned": "Street 4",
    "street_5_cleaned": "Street 5",
    "po_box_extracted": "PO Box",
    "suite": "Suite",
    "building": "Building",
    "floor": "Floor",
    "room": "Room",
    "unit": "Unit",
    "mail_stop": "Mail Stop",
    "unloading_point": "Unloading Point",
    "mail_code": "Mail Code",
    # ── Geography & remaining SAP master-data (carried through) ───────
    "country_region_key": "Country/Region Key",
    "postal_code": "Postal Code",
    "city": "City",
    "region": "Region",
    "language_key": "Language Key",
    "reconciliation_acct": "Reconciliation acct",
    "tax_jurisdiction": "Tax Jurisdiction",
    "central_delivery_block": "Central delivery block",
    "delivery_priority": "Delivery Priority",
    "shipping_conditions": "Shipping Conditions",
    "delivering_plant": "Delivering Plant",
    "created_on": "Created On",
    "created_by": "Created By",
    "vat_registration_no": "VAT Registration No.",
    "search_term_1": "Search Term 1",
    "search_term_2": "Search Term 2",
    "terms_of_payment": "Terms of Payment",
    # ── Review metadata (enrichment output) ──────────────────────────
    "flag_for_review": "Flag for Review",
    "flag_reason": "Flag Reason",
    "error": "Error",
    "record_type": "Record Type",
    # Registry identifiers — ror_id for institutions, lei_id for companies.
    "ror_id": "ROR ID",
    "lei_id": "LEI ID",
}
