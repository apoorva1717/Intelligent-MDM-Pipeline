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

# Static instructions first and the customer last, so every lookup shares the
# same prefix and it is served from the prompt cache.
LLM_WEB_USER_PROMPT_TEMPLATE = (
    "Search the web for a page where the organisation at the end of this "
    "message itself states its USt-IdNr. and report it.\n"
    "Budget: at most {max_searches} web searches, and at most {max_opens} "
    "pages opened on top of them; opening a page is not a search. A further "
    "call is refused. Plan the calls: search for the organisation's own "
    "Impressum (or procurement page) first. When the budget is used up, "
    "answer with what you have; if no page you saw states the number, every "
    "field is null.\n"
    "Return JSON:\n"
    "{{\n"
    '  "vat_id": "str or null",\n'
    '  "source_url": "str or null",\n'
    '  "evidence_quote": "str or null",\n'
    '  "attributed_to": "str or null",\n'
    '  "published_address": "str or null"\n'
    "}}\n"
    "Rules:\n"
    "1. Sources, in this order: (a) the organisation's own website: its "
    "legal notice (Impressum / Legal Notice) first; when that shows no "
    "USt-IdNr., its other pages and PDFs on the same domain, such as "
    "supplier information (Lieferanteninformationen), purchasing / "
    "procurement (Einkauf, Beschaffung), invoicing address "
    "(Rechnungsanschrift), terms and conditions (AGB) and order forms. "
    "Universities, hospitals and public bodies usually publish it on the "
    "procurement page, not in the Impressum. (b) The Peppol Directory "
    "(directory.peppol.eu): a participant under scheme 9930 is identified "
    "by its German VAT ID. (c) The organisation's buyer profile on a public "
    "procurement portal (bund.de, DTVP, evergabe, subreport) or its filing "
    "in the Bundesanzeiger / Unternehmensregister. (d) Company directories "
    "(northdata, firmenwissen, companyhouse, implisense, wer-zu-wem, …) only "
    "point you to the organisation's own website; their copy of a number is "
    "not a source. (e) Web archive snapshots (web.archive.org) only when the "
    "record status below says deleted. The Handelsregister confirms the "
    "legal name but publishes no VAT IDs. Never use marketplaces or social "
    "networks, and never a search result snippet: open the page.\n"
    "2. The number must belong to exactly this legal entity: the same name "
    "and the same legal form (GmbH, AG, SE, KG …). The number of a parent, "
    "subsidiary, sister company, branch, predecessor or successor that "
    "shares the brand is not this company's number. Several companies can "
    "also share this name; the right one is at the street / postal code / "
    "city below. If you cannot tell which page belongs to this legal "
    "entity at that address, return null rather than pick one. When the "
    "name below is a site of a company (Werk, Niederlassung, Standort, "
    "Filiale …) or a department in Name 2–4 (Institut, Lehrstuhl, Klinik "
    "für …), report the number of the legal entity it belongs to: a branch "
    "or department has no VAT ID of its own.\n"
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
    "a number you remember. If no page states it, every field is null.\n\n"
    "Organisation (from our customer master data):\n"
    "Name 1: {name_1}\n"
    "Name 2: {name_2}\n"
    "Name 3: {name_3}\n"
    "Name 4: {name_4}\n"
    "Street: {street}\n"
    "Postal Code: {postal_code}\n"
    "City: {city}\n"
    "Country: {country}\n"
    "Website / email domain (optional): {domain}\n"
    "Commercial register number (optional): {register_number}\n"
    "Record status: {record_status}"
)

#: Structured output for the LLM_WEB answer (strict JSON schema).
LLM_WEB_ANSWER_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["vat_id", "source_url", "evidence_quote", "attributed_to", "published_address"],
    "properties": {k: {"type": ["string", "null"]} for k in (
        "vat_id", "source_url", "evidence_quote", "attributed_to", "published_address")},
}

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
    # v3 — sources by who publishes the number: own domain (Impressum, then
    # supplier / procurement pages and PDFs) → Peppol Directory → Vergabeportal
    # buyer profile / Bundesanzeiger → directories as pointers only → archive
    # snapshots for deleted records only; the record status input. The
    # search budget sentence; static instructions first, customer data last.
    # v4 — the budget counts searches only; page opens have their own cap.
    "vat_llm_web", "v4", LLM_WEB_SYSTEM_PROMPT, LLM_WEB_USER_PROMPT_TEMPLATE,
)

IMPRESSUM_READ_PROMPT_VERSION = prompt_version(
    # v2 — published_address; W-IdNr. / LEI / GLN / D-U-N-S excluded.
    "vat_impressum_read", "v2",
    IMPRESSUM_READ_SYSTEM_PROMPT, IMPRESSUM_READ_USER_PROMPT_TEMPLATE,
)


# ── LLM_WEB "hybrid" (vat/lookup_web.py, vat/web_query.py) ───────────────────
# Search 1 is built by code and only RUN by the model; search 2 is the
# model's own query. Neither call opens pages: our code fetches the results,
# cuts them to the VAT / Impressum sections and gives those to the reader.
LLM_WEB_RUN_SYSTEM_PROMPT = (
    "You run web searches for a research pipeline. You run exactly the "
    "search you are told to, once, and open no pages. Return valid JSON "
    "only. No markdown."
)

LLM_WEB_RUN_USER_PROMPT_TEMPLATE = (
    "Run exactly one web search with this query, character for character, "
    "and nothing else. Do not open any result. Then return JSON with "
    '"query_run" (the query you ran) and "results". ' + '"results" lists every result the search returned, in order: "url" exactly as shown, "title" as shown, and "website": when the result is a company-directory or register entry whose title or snippet names the organisation\'s own website, that website\'s URL; else null.' + "\n"
    "Query: {query}"
)

#: One search result as the model saw it (the API itself reports URLs only).
_RESULT_ITEM: dict = {
    "type": "object", "additionalProperties": False, "required": ["url", "title", "website"],
    "properties": {"url": {"type": "string"}, "title": {"type": ["string", "null"]},
                   "website": {"type": ["string", "null"]}},
}

LLM_WEB_RUN_SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["query_run", "results"],
    "properties": {"query_run": {"type": "string"},
                   "results": {"type": "array", "items": _RESULT_ITEM}},
}

LLM_WEB_SECOND_SYSTEM_PROMPT = (
    "You find a German organisation's VAT identification number (USt-IdNr.). "
    "A first web search did not find the organisation's own website. You make "
    "ONE better web search and open no pages; the pipeline reads the results. "
    "Return valid JSON only. No markdown."
)

# Static instructions first and the customer last (prompt cache).
LLM_WEB_SECOND_USER_PROMPT_TEMPLATE = (
    "Make one web search that is more likely than the first to return this "
    "organisation's own website, ideally its Impressum / legal notice or, for "
    "universities, hospitals, research institutes and public bodies, its "
    "procurement, supplier-information or tax-number page (Einkauf, "
    "Lieferanten, Steuernummern). Rules:\n"
    "1. You may use the organisation's current or full legal name if you "
    "know it, the expansion of an acronym, or the parent organisation of a "
    "department, institute or clinic; and the terms Impressum, USt-IdNr or "
    "Steuernummer.\n"
    "2. Do not repeat the first search, or make one that differs only in "
    "word order or quotes.\n"
    "3. Do not include the city.\n"
    "4. One query only: run it once and open no result.\n"
    "{zero_results_rule}"
    "Then return JSON with: query (the query you ran, exactly), name_used "
    "(the organisation name in it, as you wrote it), why_this_query (one "
    "sentence: why this finds the website when the first search did not), "
    "domain_hint (the organisation's official website domain, e.g. "
    "\"uni-heidelberg.de\", only if you know it; else null; our pipeline "
    "checks it and never takes it on trust), "
    "and results. " + '"results" lists every result the search returned, in order: "url" exactly as shown, "title" as shown, and "website": when the result is a company-directory or register entry whose title or snippet names the organisation\'s own website, that website\'s URL; else null.' + "\n\n"
    "First search: {query_1}\n"
    "What it returned (none of it is this organisation's own website):\n{results_1}\n\n"
    "Organisation (from our customer master data):\n"
    "Name 1: {name_1}\n"
    "Name 2: {name_2}\n"
    "Name 3: {name_3}\n"
    "Name 4: {name_4}\n"
    "City: {city}\n"
    "Website / email domain (optional): {domain}"
)

LLM_WEB_SECOND_SCHEMA: dict = {
    "type": "object", "additionalProperties": False,
    "required": ["query", "name_used", "why_this_query", "domain_hint", "results"],
    "properties": {**{k: {"type": "string"} for k in ("query", "name_used", "why_this_query")},
                   "domain_hint": {"type": ["string", "null"]},
                   "results": {"type": "array", "items": _RESULT_ITEM}},
}

# ── Helper calls on the small model (Step AF): no tools, reasoning "none" ───

LLM_WEB_RANK_SYSTEM_PROMPT = (
    "You rank web search results for a data-quality pipeline that looks up a "
    "German organisation's legal notice (Impressum). You only order and label "
    "the results; the pipeline opens the pages and checks them itself. "
    "Return valid JSON only. No markdown."
)

LLM_WEB_RANK_USER_PROMPT_TEMPLATE = (
    "Pick at most 3 results, best first, that are most likely to lead to this "
    "organisation's own Impressum / legal notice: its Impressum page, its own "
    "website, a company-directory entry that names its website, or its parent "
    "organisation's site (a university for a clinic or institute, the EU for "
    "an EU body). For each: url exactly as listed; type: impressum, own_site, "
    "directory_pointer, parent_site or other; belongs_to_entity: yes, likely, "
    "no or unknown (is the site this organisation's own or its parent's?); "
    "reason: at most 12 words. Results of other organisations with a similar "
    "name are not this organisation's (belongs_to_entity no). A result whose "
    "host or path contains legal, impressum, imprint or legal-notice comes "
    "before every other result of the organisation. Pick none when none "
    "fits.\n\n"
    "Organisation (from our customer master data):\n"
    "Search name: {search_name}\n"
    "Acceptable names: {acceptable}\n"
    "City: {city}\n"
    "Segment: {segment}\n\n"
    "Results:\n{results}"
)

LLM_WEB_RANK_SCHEMA: dict = {
    "type": "object", "additionalProperties": False, "required": ["ranked"],
    "properties": {"ranked": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["url", "type", "belongs_to_entity", "reason"],
        "properties": {
            "url": {"type": "string"},
            "type": {"type": "string",
                     "enum": ["impressum", "own_site", "directory_pointer", "parent_site", "other"]},
            "belongs_to_entity": {"type": "string", "enum": ["yes", "likely", "no", "unknown"]},
            "reason": {"type": "string"},
        }}}},
}

NAME2_SYSTEM_PROMPT = (
    "You read German customer master data. SAP cuts Name 1 at 35 characters and "
    "the rest of a name often runs on in Name 2-4; Name 2-4 may also hold a "
    "department, a goods-in desk, a site or a separate legal entity. You say "
    "which, and for a name that runs on, write the full legal name. You never "
    "look anything up. Return valid JSON only. No markdown."
)

#: Ten records from the DE export (CMD_DE_2026-10-21), labelled by hand.
NAME2_EXAMPLES = (
    ("Chemisches und Veterinaer | Untersuchungsamt Sigmaringen | SIGMARINGEN",
     "CONTINUATION", "Chemisches und Veterinäruntersuchungsamt Sigmaringen", 0.95,
     "Name 2 completes the authority's name"),
    ("Landesuntersuchungsanstalt für das | Gesundheits-und Veterinärwesen | Standort Chemnitz | CHEMNITZ",
     "CONTINUATION", "Landesuntersuchungsanstalt für das Gesundheits- und Veterinärwesen", 0.9,
     "Name 1 ends on 'für das'; Name 3 is a site"),
    ("Polizeiinspektion Zentrale Dienste | Sachsen-Anhalt | MAGDEBURG",
     "CONTINUATION", "Polizeiinspektion Zentrale Dienste Sachsen-Anhalt", 0.85,
     "the state is part of the authority's name"),
    ("Pfizer Manufacturing Deutschland | Grundbesitz GmbH & Co KG | FREIBURG",
     "CONTINUATION", "Pfizer Manufacturing Deutschland Grundbesitz GmbH & Co. KG", 0.95,
     "Name 2 ends the company name with its legal form"),
    ("Eberhard Karls Universität Tübingen | Proteom Centrum Tübingen | TÜBINGEN",
     "DEPARTMENT", None, 0.9, "a research centre of the university"),
    ("Charité-Universitätsmedizin Berlin | Medizintechnik | BERLIN",
     "DEPARTMENT", None, 0.9, "the hospital's medical-technology department"),
    ("WIKA Alexander Wiegand SE & Co. KG | Wika Retoure für Wareneingang WIKA | WÖRTH",
     "DEPARTMENT", None, 0.95, "a returns / goods-in desk of the company"),
    ("Gemeinschaftslabor Nanomaterialien | FZ Karlsruhe / TU Darmstadt | DARMSTADT",
     "OTHER", None, 0.6, "a joint lab of two institutions; no single legal entity"),
    ("NMI Naturwissenschaftliches und | und Medizinisches Institut an der | Universität Tübingen | REUTLINGEN",
     "CONTINUATION", "NMI Naturwissenschaftliches und Medizinisches Institut an der Universität Tübingen", 0.95,
     "the institute's name runs on over Name 2 and 3"),
    ("Albert-Ludwigs-Universität Freiburg | Dez 4 Bau und Infrastruktur | D4.5 Zentrale Beschaffung | FREIBURG",
     "DEPARTMENT", None, 0.95, "a Dezernat and its procurement office"),
)


def _name2_example(record, cls, name, conf, why) -> str:
    import json

    answer = json.dumps({"class": cls, "legal_name": name, "confidence": conf, "reason": why}, ensure_ascii=False)
    # Braces doubled: the template is filled with str.format.
    return f"Record: {record}\nAnswer: {answer}".replace("{", "{{").replace("}", "}}")


NAME2_USER_PROMPT_TEMPLATE = (
    "class: CONTINUATION (Name 2-4 carry on the legal entity's name), "
    "DEPARTMENT (a department, institute, desk or site of the Name 1 entity), "
    "SUB_ENTITY (Name 2-4 name a separate legal entity under Name 1) or OTHER. "
    "legal_name: CONTINUATION only, the full legal name with a word cut at 35 "
    "characters repaired; else null. confidence: 0 to 1. reason: at most 15 "
    "words.\n\nExamples:\n"
    + "\n\n".join(_name2_example(*e) for e in NAME2_EXAMPLES)
    + "\n\nRecord: {record}\nAnswer:"
)

NAME2_SCHEMA: dict = {
    "type": "object", "additionalProperties": False,
    "required": ["class", "legal_name", "confidence", "reason"],
    "properties": {
        "class": {"type": "string", "enum": ["CONTINUATION", "DEPARTMENT", "SUB_ENTITY", "OTHER"]},
        "legal_name": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
}

NAME2_PROMPT_VERSION = prompt_version(
    # v1 — Step AF rule 10b: ten hand-labelled export records as few-shot examples.
    "vat_name2_classifier", "v1", NAME2_SYSTEM_PROMPT, NAME2_USER_PROMPT_TEMPLATE,
)

#: Search 2's extra rule when search 1 returned nothing.
LLM_WEB_ZERO_RESULTS_RULE = (
    "5. The first search returned no results at all: leave out the quotes and "
    "the legal form (GmbH, AG, KG …).\n"
)

LLM_WEB_READ_SYSTEM_PROMPT = (
    "You read extracts of web pages our pipeline fetched and report ONLY "
    "the VAT identification number (USt-IdNr.) they literally state for one "
    "organisation. You are a reader, not a source of knowledge: nothing you "
    "know about any company may appear in your answer. Never infer, "
    "complete, correct or reformat digits. Return valid JSON only. No "
    "markdown."
)

LLM_WEB_READ_USER_PROMPT_TEMPLATE = (
    "Each page below is given as its URL, the strings on the whole page "
    "that look like a German VAT ID (DE + 9 digits; found by pattern, "
    "possibly someone else's), and extracts: the page head and the text "
    "around every USt-IdNr. / VAT / Impressum mention.\n"
    "Rules:\n"
    "1. vat_id is the USt-IdNr. a page states for the organisation named at "
    "the end, or for the legal entity it belongs to (its company for a "
    "site, its university or hospital for an institute or clinic), exactly "
    "as written, with its DE prefix. source_url is that page's URL as "
    "listed. A Steuernummer, W-IdNr. (with a 5-digit suffix), HRB/HRA, LEI, "
    "GLN, D-U-N-S, EORI, phone or fax number is not a VAT ID.\n"
    "2. When a page lists several organisations' numbers, take the one the "
    "page gives for this organisation; list every other VAT ID on that page "
    "in other_vat_ids_on_page. If you cannot tell which number is this "
    "organisation's, vat_id is null.\n"
    "2a. candidates lists every VAT ID the pages state, each with source_url "
    "and the organisation name written next to it (attributed_to), whether "
    "or not you chose one: our pipeline puts them before a person when it "
    "cannot tell either.\n"
    "3. evidence_quote is a passage copied VERBATIM from the extract that "
    "contains vat_id (at most 200 characters). attributed_to is the "
    "organisation name, as written on the page, the number is given for. "
    "published_address is the postal address the page gives for it.\n"
    "4. no_vat_statement: when a page says this organisation has no "
    "USt-IdNr., is not liable to VAT or is a Kleinunternehmer (§ 19 UStG), "
    "copy that sentence verbatim; else null.\n"
    "5. Nothing stated → vat_id, source_url, evidence_quote, attributed_to "
    "and published_address are null. Never complete or guess digits.\n\n"
    "Organisation: {name}\n\n"
    "{pages}"
)

_CANDIDATE: dict = {
    "type": "object", "additionalProperties": False,
    "required": ["vat_id", "source_url", "attributed_to"],
    "properties": {"vat_id": {"type": "string"}, "source_url": {"type": ["string", "null"]},
                   "attributed_to": {"type": ["string", "null"]}},
}

LLM_WEB_READ_SCHEMA: dict = {
    "type": "object", "additionalProperties": False,
    "required": ["vat_id", "source_url", "evidence_quote", "attributed_to", "published_address",
                 "other_vat_ids_on_page", "no_vat_statement", "candidates"],
    "properties": {
        **{k: {"type": ["string", "null"]} for k in (
            "vat_id", "source_url", "evidence_quote", "attributed_to", "published_address",
            "no_vat_statement")},
        "other_vat_ids_on_page": {"type": "array", "items": {"type": "string"}},
        "candidates": {"type": "array", "items": _CANDIDATE},
    },
}

LLM_WEB_HYBRID_PROMPT_VERSION = prompt_version(
    # v1 — Step AA: search 1 by code, search 2 by the model (name_used,
    # why_this_query), the reader on code-fetched page sections.
    # v2 — Step AB: search 1 is name + city; both searches list their
    # results (title, a directory's website link); search 2 looks for the
    # entity's own website and may use Impressum / USt-IdNr / Steuernummer.
    # v3 — Step AC: the reader lists every VAT ID with its company (candidates).
    # v4 — Step AF: search 2 returns domain_hint; the ranking call on the helper model.
    "vat_llm_web_hybrid", "v4",
    LLM_WEB_RUN_SYSTEM_PROMPT, LLM_WEB_RUN_USER_PROMPT_TEMPLATE,
    LLM_WEB_SECOND_SYSTEM_PROMPT, LLM_WEB_SECOND_USER_PROMPT_TEMPLATE,
    LLM_WEB_READ_SYSTEM_PROMPT, LLM_WEB_READ_USER_PROMPT_TEMPLATE,
    LLM_WEB_RANK_SYSTEM_PROMPT, LLM_WEB_RANK_USER_PROMPT_TEMPLATE,
)
