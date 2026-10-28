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
    '  "published_address": "str or null",\n'
    '  "other_vat_ids_on_page": ["str"]\n'
    "}}\n"
    "Rules:\n"
    "1. vat_id is the Umsatzsteuer-Identifikationsnummer / USt-IdNr. / VAT "
    "ID / VAT number exactly as written on this page, including its country "
    "prefix if the page shows one. Copy it character for character. If the "
    "page shows no VAT ID, vat_id is null.\n"
    "2. A Steuernummer, tax number, Wirtschafts-Identifikationsnummer "
    "(W-IdNr., DE + 9 digits + a 5-digit suffix), Handelsregister number "
    "(HRB/HRA), LEI, GLN, D-U-N-S number, WEEE number, EORI number, phone or "
    "fax number is NOT a VAT ID. Never return one of those as vat_id.\n"
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
    "If the number on the page is incomplete or unreadable, vat_id is null.\n"
    "7. published_address is the postal address the page gives for the "
    "organisation in attributed_to (street, house number, postal code, "
    "city), copied from the page; separate its lines with \", \". null if "
    "the page gives none."
)

# ── LLM_WEB — the model searches the web; never answers from memory. ────────
# See vat/lookup_web.py. The model is given the SAP name and address (it needs
# them to search) and reports what ONE page it visited states. Whether that
# page really says so, and whether the number is this customer's, is decided
# afterwards by the code: it fetches source_url itself and runs the grounded
# gates on it. A number without a visited source is never used.
LLM_WEB_SYSTEM_PROMPT = (
    "You find a German company's VAT identification number (USt-IdNr.) by "
    "searching the web and reading the pages you find, the way a careful "
    "researcher would. You never answer from memory: a number you did not "
    "read on a page you opened or found in this search must not appear in "
    "your answer. If no page you found states it, say so with null. Return "
    "valid JSON only. No markdown."
)

LLM_WEB_USER_PROMPT_TEMPLATE = (
    "Company (from our customer master data):\n"
    "Name 1: {name_1}\n"
    "Name 2: {name_2}\n"
    "Name 3: {name_3}\n"
    "Name 4: {name_4}\n"
    "Street: {street}\n"
    "Postal Code: {postal_code}\n"
    "City: {city}\n"
    "Country: {country}\n"
    "Website / email domain (optional): {domain}\n"
    "Commercial register number (optional): {register_number}\n\n"
    "Search the web for this company's own legal notice (Impressum / "
    "Imprint) and report the USt-IdNr. it states.\n"
    "Return JSON:\n"
    "{{\n"
    '  "vat_id": "str or null",\n'
    '  "source_url": "str or null",\n'
    '  "evidence_quote": "str or null",\n'
    '  "attributed_to": "str or null",\n'
    '  "published_address": "str or null"\n'
    "}}\n"
    "Rules:\n"
    "1. Sources, in order of preference: the company's own legal notice "
    "(Impressum / Legal Notice) on its own website; then official "
    "registers (Handelsregister, Unternehmensregister), which confirm the "
    "legal entity even when they show no VAT ID. Company directories "
    "(northdata, firmenwissen, wlw, companyhouse, …), procurement portals "
    "and other third-party databases only point you to the company; their "
    "copy of a number is not a source.\n"
    "2. The number must belong to exactly this legal entity: the same name "
    "and the same legal form (GmbH, AG, SE, KG …). The number of a parent, "
    "subsidiary, sister company, branch, predecessor or successor that "
    "shares the brand is not this company's number. Several companies can "
    "also share this name; the right one is at the street / postal code / "
    "city above. If you cannot tell which page belongs to this legal "
    "entity at that address, return null rather than pick one.\n"
    "3. vat_id is the USt-IdNr. exactly as written on the page (DE + 9 "
    "digits). A Steuernummer, Wirtschafts-Identifikationsnummer (W-IdNr., "
    "with a 5-digit suffix), Handelsregister (HRB/HRA), LEI, GLN, D-U-N-S, "
    "WEEE, EORI, phone or fax number is not a VAT ID.\n"
    "4. source_url is the URL of the page that states vat_id, exactly as "
    "you visited it.\n"
    "5. evidence_quote is one short passage copied VERBATIM from that page "
    "that contains vat_id (at most 200 characters).\n"
    "6. attributed_to is the organisation name, exactly as written on that "
    "page, that the page says the number belongs to.\n"
    "7. published_address is the postal address that page gives for that "
    "organisation, copied from the page; separate its lines with \", \".\n"
    "8. Never complete, correct, derive or guess digits, and never fill in "
    "a number you remember. If no page states it, every field is null."
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
    'Answer JSON {{"vat_id": "str", "confidence": "high|medium|low"}}. '
    "vat_id is never null: this is a research experiment that measures how "
    "accurate a model's recall is, so a wrong number is a valid answer and an "
    "empty one is not. If you are not sure, give your best candidate number "
    '(DE followed by 9 digits) and set confidence to "low".'
)

LLM_ONLY_PROMPT_VERSION = prompt_version(
    # v2 — always a best guess (confidence "low" when unsure) instead of null:
    # the experiment measures what the model proposes, including when unsure.
    # v3 — no null escape: gpt-6-sol took v2's "null only if you have no
    # candidate" on 31 of 38 rows; with it removed it guesses, at "low".
    "vat_llm_only_experiment", "v3",
    LLM_ONLY_SYSTEM_PROMPT, LLM_ONLY_USER_PROMPT_TEMPLATE,
)

LLM_WEB_PROMPT_VERSION = prompt_version(
    # v2 — design paper alignment: domain and register inputs, the source
    # hierarchy, the exact-legal-entity rule (legal form; no parent /
    # subsidiary / branch), W-IdNr. / LEI / GLN / D-U-N-S excluded, and the
    # published address.
    "vat_llm_web", "v2", LLM_WEB_SYSTEM_PROMPT, LLM_WEB_USER_PROMPT_TEMPLATE,
)

IMPRESSUM_READ_PROMPT_VERSION = prompt_version(
    # v2 — published_address; W-IdNr. / LEI / GLN / D-U-N-S excluded.
    "vat_impressum_read", "v2",
    IMPRESSUM_READ_SYSTEM_PROMPT, IMPRESSUM_READ_USER_PROMPT_TEMPLATE,
)
