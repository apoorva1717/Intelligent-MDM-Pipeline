"""Prompts for the missing-VAT lookups. Versioned like ``llm/prompts.py``.

The grounded reader follows the constrained-reader pattern of
``llm.prompts.PAGE_READ_SYSTEM_PROMPT``: the model reads ONE fetched page and
reports what that page literally states. It is never shown the SAP record's
name or address, so it cannot be steered towards an answer; whether the ID it
reads belongs to the customer is decided afterwards by deterministic gates
(``vat/lookup_grounded.py``), not by the model.
"""

from __future__ import annotations

from llm.prompts import prompt_version

IMPRESSUM_READ_SYSTEM_PROMPT = (
    "You read the text of one web page (usually a German Impressum / legal "
    "notice) and report ONLY the VAT identification number it literally "
    "states. You are a reader, not a source of knowledge: nothing you know "
    "about any company may appear in your answer. Never infer, complete, "
    "correct or reformat digits. Return valid JSON only. No markdown."
)

IMPRESSUM_READ_USER_PROMPT_TEMPLATE = (
    "Page URL: {url}\n"
    "Page title: {title}\n"
    "Page text:\n"
    "---\n"
    "{text}\n"
    "---\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "vat_id": "str or null",\n'
    '  "attributed_to": "str or null",\n'
    '  "evidence_quote": "str or null",\n'
    '  "other_vat_ids_on_page": ["str"]\n'
    "}}\n"
    "Rules:\n"
    "1. vat_id is the Umsatzsteuer-Identifikationsnummer / USt-IdNr. / VAT "
    "ID / VAT number exactly as written on this page, including its country "
    "prefix if the page shows one. Copy it character for character. If the "
    "page shows no VAT ID, vat_id is null.\n"
    "2. A Steuernummer, tax number, Handelsregister number (HRB/HRA), "
    "WEEE number, EORI number, phone or fax number is NOT a VAT ID. Never "
    "return one of those as vat_id.\n"
    "3. If the page lists several VAT IDs, vat_id is the one the page gives "
    "for the organisation the page is the legal notice of (the provider / "
    "Diensteanbieter / operator). Every other VAT ID on the page goes in "
    "other_vat_ids_on_page, each exactly as written. Otherwise that list is "
    "empty.\n"
    "4. attributed_to is the organisation name, exactly as written on the "
    "page, that the page states vat_id belongs to. null if the page does "
    "not say whose number it is.\n"
    "5. evidence_quote is one short passage copied VERBATIM from the page "
    "text above that contains vat_id (at most 200 characters). Do not "
    "paraphrase, translate, shorten or fix it. null when vat_id is null.\n"
    "6. Never complete a partial number and never guess a missing digit. "
    "If the number on the page is incomplete or unreadable, vat_id is null."
)

# ── Mode B — LLM_ONLY. EXPERIMENT ONLY. ─────────────────────────────────────
# Asks the model from memory, with no search and no page, to MEASURE how
# unreliable that is. Never used on a production path; its answers are never
# written to SAP. See vat/lookup_llm_only.py.
LLM_ONLY_SYSTEM_PROMPT = (
    "You answer questions about companies. Return valid JSON only. No markdown."
)

LLM_ONLY_USER_PROMPT_TEMPLATE = (
    "Company:\n"
    "Name 1: {name_1}\n"
    "Name 2: {name_2}\n"
    "Name 3: {name_3}\n"
    "Name 4: {name_4}\n"
    "Street: {street}\n"
    "Postal Code: {postal_code}\n"
    "City: {city}\n"
    "Country: {country}\n\n"
    "What is this company's German VAT identification number (USt-IdNr.)? "
    'Answer JSON {{"vat_id": "str or null", "confidence": "high|medium|low"}}. '
    "Answer null if you do not know."
)

LLM_ONLY_PROMPT_VERSION = prompt_version(
    "vat_llm_only_experiment", "v1",
    LLM_ONLY_SYSTEM_PROMPT, LLM_ONLY_USER_PROMPT_TEMPLATE,
)

IMPRESSUM_READ_PROMPT_VERSION = prompt_version(
    "vat_impressum_read", "v1",
    IMPRESSUM_READ_SYSTEM_PROMPT, IMPRESSUM_READ_USER_PROMPT_TEMPLATE,
)
