"""Single output schema for the /enrich and /enrich/file endpoints.

This mapping defines every output column in order:

    key   = EnrichmentResult field name (internal, snake_case)
    value = the output name — both the column header written to the XLSX
            and the JSON key in the /enrich response body (applied as a
            serialization alias on ``EnrichmentResult``)

The JSON response and the file therefore always carry exactly the same
columns under exactly the same names. Edit a value here to rename an
output column in both places at once.
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
    "name5_enriched": "Name 5",
    # "Domain" and "Website URL" are merged into a single "Domain" column. It
    # carries the registrable ``domain`` (mit.edu) — never a full URL: the
    # homepage ``website_url`` is derived from it (https://mit.edu) and kept
    # internal, so the column can no longer ship a deep ROR link
    # (…/home/index.en.html) or a sub-site host (investors.lockheedmartin.com).
    # See utils/domain_resolver.py.
    "domain": "Domain",
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
    "flag_codes": "Flag Codes",
    "flagged_fields": "Flagged Fields",
    "flag_reason": "Flag Reason",
    "error": "Error",
    "record_type": "Record Type",
    # Registry identifiers — ror_id for institutions, lei_id for companies.
    "ror_id": "ROR ID",
    "lei_id": "LEI ID",
}
