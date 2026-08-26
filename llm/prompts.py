"""All LLM prompt strings as module-level constants.

Centralised here so they can be versioned, reviewed, and tested independently.
"""

# ---------------------------------------------------------------------------
# UC 0 — Name overflow into the adjacent name field
# ---------------------------------------------------------------------------

OVERFLOW_CHECK_SYSTEM_PROMPT = (
    "You detect whether two adjacent customer-master name fields "
    "read as one continuous organisation name split across the "
    "fields, or as two separate entities. Return valid JSON only."
)

# {upper_label} / {lower_label} name the two SAP columns being compared
# ("Name 1" and "Name 2", "Name 3" and "Name 4", …) so the same prompt
# serves every adjacent pair in the block. {name1} is always the upper
# field's value and {name2} the lower field's.
OVERFLOW_CHECK_USER_PROMPT_TEMPLATE = (
    "{upper_label}: {name1}\n"
    "{lower_label}: {name2}\n\n"
    "Read these two fields together as if they were the full name of "
    "one organisation. Does the concatenation "
    "'{upper_label} + {lower_label}' form a single continuous "
    "organisation name (an overflow), or do {upper_label} and "
    "{lower_label} describe two distinct entities (e.g. an "
    "institution + a department, or a department + a lab)?\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "is_overflow": true | false,\n'
    '  "confidence": "high" | "medium" | "low",\n'
    '  "reasoning": "str"\n'
    "}}\n\n"
    "Rules:\n"
    "1. is_overflow=true only when {upper_label} + {lower_label} reads "
    "naturally as ONE name — e.g. 'Adams Air' + 'Hydraulics Inc' → "
    "'Adams Air Hydraulics Inc', or 'Department of Molecular' + "
    "'Biology and Genetics' → 'Department of Molecular Biology and "
    "Genetics'.\n"
    "2. is_overflow=false when {lower_label} is a department, division, "
    "research group, lab, contact person, or any standalone unit "
    "within {upper_label}.\n"
    "3. When in doubt, prefer false. The goal is to surface likely "
    "overflows, not to flag every case with a shared word.\n"
    "4. Legal suffixes (Inc, Ltd, LLC, Corp, Co, GmbH, AG) appearing "
    "in {lower_label} with no department qualifier are a strong "
    "overflow signal.\n"
    "5. A lower field opening with a connector ('and', '&', 'of', "
    "'for', 'der', 'und') or a lowercase word is a strong overflow "
    "signal at any slot boundary."
)


# ---------------------------------------------------------------------------
# Tier 2A — Contact person affiliation extraction
# ---------------------------------------------------------------------------

TIER2A_SYSTEM_PROMPT = (
    "Data extraction assistant for MDM pipeline. "
    "Return valid JSON only. No markdown or code fences."
)

TIER2A_USER_PROMPT_TEMPLATE = (
    "Extract affiliation for: {contact}\n"
    "Institution: {institution}\n"
    "Existing Name 2: {name2}\n"
    "Existing Name 3: {name3}\n"
    "{existing_departments}"
    "Page: {page_text}\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "person_found": bool,\n'
    '  "official_dept": "str or null",\n'
    '  "official_group": "str or null",\n'
    '  "title": "str or null",\n'
    '  "name2_match": "exact|partial|no_match|unknown",\n'
    '  "name2_match_score": 0-100,\n'
    '  "confidence": "high|medium|low",\n'
    '  "reasoning": "str"\n'
    "}}\n"
    "Rules:\n"
    "1. If the page is not about the named person, set "
    "person_found=false and all other fields to JSON null.\n"
    "2. For official_dept, pick the institution's canonical department "
    "name using ALL available signals in the input: URL host, URL "
    "path, page title, H1, breadcrumb, and body. An institution's "
    "URL host often includes a leading subdomain that abbreviates the "
    "department (e.g. a leading token before the main institution "
    "domain) — if so, infer the full canonical department name from "
    "that abbreviation.\n"
    "3. ALWAYS prefer the most specific academic unit available. "
    "Granularity ranking (most to least specific):\n"
    "     a) 'Department of X' or 'Division of X'  -- STRONGLY PREFERRED\n"
    "     b) 'Institute of X' or 'Center for X' (peer-level)\n"
    "     c) 'School of X', 'College of X', 'Faculty of X' (parent units -- FALLBACK ONLY)\n"
    "   If the page mentions BOTH a department and an enclosing school/"
    "college/faculty for this person (e.g. 'Department of Neuroscience, "
    "College of Medicine'), return the DEPARTMENT, never the college. "
    "A faculty member is always in a department within the college; "
    "the college alone is too coarse for downstream lookup. Only "
    "return a school/college/faculty when no department is "
    "identifiable on the page.\n"
    "4. Expand any subdomain abbreviation to the institution's actual "
    "canonical department wording.\n"
    "5. Reject generic role labels such as 'Research', 'Admin', "
    "'Staff', 'Faculty', 'Team', or 'Office'. They describe what the "
    "person does, not the unit they belong to. If the body contains "
    "only a role label, derive the unit from the URL host instead.\n"
    "6. Do not return a bare subject word alone ('Anesthesia', "
    "'Chemistry') and do not return a job title ('Professor of X').\n"
    "7. official_group may be set verbatim from the body when a "
    "specific research group, lab, or centre is clearly named. "
    "Otherwise null. Use JSON null, never the string 'null'."
)

# ---------------------------------------------------------------------------
# Tier 2B — Department search extraction
# ---------------------------------------------------------------------------

TIER2B_SYSTEM_PROMPT = (
    "Data extraction assistant for MDM pipeline. "
    "Return valid JSON only. No markdown or code fences."
)

TIER2B_USER_PROMPT_TEMPLATE = (
    "Extract the official department or division name that this "
    "page represents.\n"
    "Organisation: {name1}\n\n"
    "Authoritative page elements (use ONLY these as your source):\n"
    "URL path:   {url_path}\n"
    "Title tag:  {page_title}\n"
    "H1 heading: {h1}\n"
    "Breadcrumb: {breadcrumb}\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "official_name": "str or null",\n'
    '  "confidence": "high|medium|low",\n'
    '  "reasoning": "str"\n'
    "}}\n"
    "Rules:\n"
    "1. Extract ONLY from the four authoritative elements above. "
    "Do not invent, reformat, abbreviate, or expand anything.\n"
    "2. Copy the wording verbatim from whichever element clearly "
    "names the unit. Priority order: title tag > H1 > breadcrumb > "
    "URL path.\n"
    "3. If none of the elements clearly name a unit, return null."
)

# ---------------------------------------------------------------------------
# UC 13 — Lab/group/center → parent department resolution
# ---------------------------------------------------------------------------

LAB_PARENT_SYSTEM_PROMPT = (
    "Data extraction assistant for MDM pipeline. You identify the "
    "PARENT academic department of a research unit (lab, research "
    "group, centre, core, or facility) from a page on its "
    "institution's website. Return valid JSON only. No markdown."
)

LAB_PARENT_USER_PROMPT_TEMPLATE = (
    "Institution: {name1}\n"
    "Research unit (a lab/group/centre/facility): {lab_name}\n\n"
    "Authoritative page elements (use ONLY these as your source):\n"
    "URL path:   {url_path}\n"
    "Title tag:  {page_title}\n"
    "H1 heading: {h1}\n"
    "Breadcrumb: {breadcrumb}\n\n"
    "Return the parent academic department, division, school, "
    "college, faculty, or institute that this research unit belongs "
    "to.\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "parent_department": "str or null",\n'
    '  "confidence": "high|medium|low",\n'
    '  "reasoning": "str"\n'
    "}}\n"
    "Rules:\n"
    "1. The parent must be an academic unit at department level or "
    "higher: 'Department of X', 'Division of X', 'School of X', "
    "'College of X', 'Faculty of X', or 'Institute of X'. NEVER "
    "another lab, group, centre, core, or facility.\n"
    "2. Look at: breadcrumb (often 'Home > Chemistry > Groups > NMR "
    "Lab' → parent is 'Department of Chemistry'), URL path "
    "(/chemistry/research/nmr-lab/ → 'Department of Chemistry'), and "
    "the title/H1 if they explicitly name the parent.\n"
    "3. confidence=high: parent is explicitly stated (in breadcrumb "
    "or title). confidence=medium: parent is implied by URL path. "
    "confidence=low: best guess.\n"
    "4. If you cannot identify a clear parent academic department, "
    "return null. Do not invent.\n"
    "5. Use JSON null, never the string 'null'."
)

# ---------------------------------------------------------------------------
# Tier 2 canonicalization — LLM-only (no page fetch)
# ---------------------------------------------------------------------------

TIER2_CANONICAL_SYSTEM_PROMPT = (
    "You normalise user-supplied academic department names to the "
    "canonical wording the institution itself uses on its own website. "
    "Return valid JSON only. No markdown or code fences."
)

TIER2_CANONICAL_USER_PROMPT_TEMPLATE = (
    "Institution (verified): {institution}\n"
    "User-supplied department text: {name2}\n\n"
    "Return the official name of this unit as the institution "
    "documents it on its own website (e.g. 'Department of X', "
    "'Division of X', 'School of X', 'Institute of X').\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "official_name": "str or null",\n'
    '  "confidence": "high|medium|low",\n'
    '  "reasoning": "str"\n'
    "}}\n"
    "Rules:\n"
    "1. Only return a name if you are confident it is the institution's "
    "actual canonical wording. When in doubt, return null.\n"
    "2. Do not invent units the institution does not have.\n"
    "3. Match the subject the user supplied — if they said 'Biochemistry', "
    "do not return 'Chemistry'.\n"
    "4. confidence=high means you are certain of the exact wording. "
    "Use medium or low if you are guessing the form."
)


# ---------------------------------------------------------------------------
# Company name1 canonicalisation — LLM-only, 0 SerpAPI
# ---------------------------------------------------------------------------

COMPANY_CANONICAL_SYSTEM_PROMPT = (
    "You normalise user-supplied company names to the canonical "
    "registered form the company uses publicly. Return valid JSON only."
)

COMPANY_CANONICAL_USER_PROMPT_TEMPLATE = (
    "User-supplied company name: {name1}\n"
    "Street: {street}\n"
    "Postal code: {postal_code}\n"
    "City: {city}\n"
    "State: {state}\n"
    "Country: {country}\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "official_name": "str or null",\n'
    '  "confidence": "high|medium|low",\n'
    '  "reasoning": "str"\n'
    "}}\n"
    "Rules:\n"
    "1. Return a confident canonical form only when you are certain "
    "it matches the intended company. Use the geographic context to "
    "disambiguate.\n"
    "2. The full street address may identify a well-known corporate "
    "headquarters and help you recognise a misspelled or abbreviated "
    "form of THAT company's name (e.g. a typo of the company "
    "headquartered there). Use it to CORRECT or disambiguate a name "
    "that is already a plausible variant of the company at that "
    "address. NEVER replace the given name with a different company "
    "just because they share a building — many firms share an "
    "address, so the name must still match.\n"
    "3. Return null if you are not sure.\n"
    "4. Do not invent companies. Do not resolve acronyms you do not "
    "recognise.\n"
    "5. confidence=high means you are certain of the exact wording."
)


# ---------------------------------------------------------------------------
# Website inference — LLM-only (Path C in website resolver)
# ---------------------------------------------------------------------------

WEBSITE_INFERENCE_SYSTEM_PROMPT = (
    "You return the official corporate website URL for a company. "
    "Return valid JSON only. Never guess or hallucinate URLs."
)

WEBSITE_INFERENCE_USER_PROMPT_TEMPLATE = (
    "Given the following company information, provide the official "
    "website URL.\n\n"
    "Company: {name1}\n"
    "City: {city}\n"
    "State: {state}\n"
    "Country: {country}\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "website_url": "str or null",\n'
    '  "confidence": "high|medium|low"\n'
    "}}\n\n"
    "Rules:\n"
    "1. Return the official corporate website URL only when you are "
    "confident the company is well-known and the URL is correct.\n"
    "2. If you are not confident or the company is obscure, return "
    "JSON null for website_url.\n"
    "3. Do not guess or hallucinate URLs. Use JSON null, never the "
    'string "null" or "UNKNOWN".\n'
    "4. Format: https://www.example.com (include scheme)."
)


# ---------------------------------------------------------------------------
# Page read — constrained reader over fetched page text (Fix 3)
# ---------------------------------------------------------------------------
#
# The model is a READER here, not a source. It is shown page text and asked
# what that text states; it is never shown the record, never asked whether the
# record is right, and never asked to supply anything the page omits. That is
# the whole design: a page is a witness, and a witness that fills gaps from
# memory is not a witness. Two failure modes the wording targets specifically:
#
#   * A brand logo, a favicon, or a page title that is only a slogan is not a
#     statement of organisational identity. Returning the site's marketing name
#     for every page would corroborate every domain, which is worthless.
#   * A model that knows a company's headquarters will supply the city when the
#     page does not mention it — and location consistency is one of the two
#     tests, so a remembered address would decide the very question being
#     asked. Absent fields must come back null.
#   * A page that names itself twice — "LabQ" across the top and "Labq Clinical
#     Diagnostics, Inc." in the copyright line — was being reported by whichever
#     form the reader met first, which is the short one. That is a real choice
#     the reader has to make and the rules were silent on it, so `Operating
#     Name` came out a clipped version of Name 1 on row after row. Rule 7
#     settles it: among the forms the page ACTUALLY PRINTS, the most complete
#     one wins. It is not a licence to expand — rule 6 still forbids assembling
#     a name or supplying a legal form the page does not show — and it only
#     became reachable once `PageContent.footer_text` started putting the
#     copyright line in front of the reader at all.

PAGE_READ_SYSTEM_PROMPT = (
    "You read the text of one web page and report ONLY what that page "
    "states about the organisation that operates it. You are a reader, not "
    "a source of knowledge: nothing you already know about any company may "
    "appear in your answer. Return valid JSON only. No markdown."
)

PAGE_READ_USER_PROMPT_TEMPLATE = (
    "Page URL: {url}\n"
    "Page title: {title}\n"
    "Page heading: {h1}\n"
    "Page text:\n"
    "---\n"
    "{text}\n"
    "---\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "stated_org_name": "str or null",\n'
    '  "stated_city": "str or null",\n'
    '  "stated_region": "str or null",\n'
    '  "stated_country": "str or null",\n'
    '  "stated_postal_code": "str or null",\n'
    '  "legal_form_present": true or false\n'
    "}}\n"
    "Rules:\n"
    "1. Every field must be supported by text on THIS page. If the page "
    "does not state it, the field is JSON null. Never fill a gap from your "
    "own knowledge of the organisation.\n"
    "2. stated_org_name is the name the page gives for the organisation "
    "that operates the site — from an imprint, a legal notice, a copyright "
    "line, an about/contact statement, or a clear self-description. A brand "
    "name in a logo, a slogan, or a product name is NOT a statement of "
    "organisational identity: return null.\n"
    "3. If the page states no organisation identity at all — a parked "
    "domain, a for-sale placeholder, a login wall, an error page, a bare "
    "landing page — return null for every field and false for "
    "legal_form_present.\n"
    "4. stated_city / stated_region / stated_country / stated_postal_code "
    "come from a postal address, imprint, or registered-office statement on "
    "this page. A list of many office locations is not a single address: "
    "return null rather than choosing one.\n"
    "5. legal_form_present is true only when stated_org_name carries an "
    "explicit legal form (Inc, LLC, Ltd, GmbH, AG, S.A., B.V., …).\n"
    "6. Report the name exactly as written on the page. Do not expand, "
    "abbreviate, translate, or tidy it.\n"
    "7. If the page states the operating organisation's name in more than "
    "one form — a short trading name in a heading or logo and a fuller one "
    "in a copyright line, an imprint, an address block, or an about "
    "statement — return the MOST COMPLETE form that appears on the page. "
    "Text marked [footer] is part of the page and is usually where the "
    "fullest form is. Choosing between forms the page prints is not "
    "expanding; rule 6 still holds, so never assemble a fuller name out of "
    "parts and never add a legal form the page does not show."
)


# ---------------------------------------------------------------------------
# Address Stage 1 — residual classification for street_2 / street_3
# ---------------------------------------------------------------------------

ADDRESS_RESIDUAL_SYSTEM_PROMPT = (
    "You classify residual values found in street address fields after "
    "deterministic extractors have already pulled out PO Box, Suite, "
    "Building, Floor, Room, Unit, Mail Stop, c/o, and Attn patterns. "
    "Return valid JSON only. No markdown."
)

ADDRESS_RESIDUAL_USER_PROMPT_TEMPLATE = (
    "Classify this value from a street address field. It was found "
    "after PO Box, Suite, Building, Floor, Room, Unit, Mail Stop, "
    "c/o, and Attn patterns were already extracted.\n\n"
    "Value: \"{value}\"\n"
    "Name 1: \"{name1}\"  Street 1: \"{street}\"  "
    "City: \"{city}\"  Country: \"{country}\"\n\n"
    "Classify as exactly one of: STREET_ADDRESS, DEPARTMENT, "
    "PERSON_NAME, ORG_NAME, LOGISTICS, MAIL_CODE, UNCLEAR\n"
    "Return JSON: {{\"classification\": \"...\", "
    "\"confidence\": 0.0-1.0}}"
)


# ---------------------------------------------------------------------------
# Tier 3 — LLM inference (last resort)
# ---------------------------------------------------------------------------

TIER3_SYSTEM_PROMPT = (
    "Help clean SAP customer master data for scientific "
    "instrument manufacturer. Return valid JSON only."
)

TIER3_USER_PROMPT_TEMPLATE = (
    "Infer official org and dept names from this record.\n"
    "Name 1: {name1}\n"
    "Name 2: {name2}\n"
    "Name 3: {name3}\n"
    "Name 4: {name4}\n"
    "Name 5: {name5}\n"
    "Contact: {contact}\n"
    "Address: {street}, {city}, {state} {zip}, {country}\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "name1_suggestion": "str or null",\n'
    '  "name2_suggestion": "str or null",\n'
    '  "name3_suggestion": "str or null",\n'
    '  "name4_suggestion": "str or null",\n'
    '  "name5_suggestion": "str or null",\n'
    '  "confidence": "high|medium|low",\n'
    '  "reasoning": "str",\n'
    '  "requires_verification": true\n'
    "}}\n"
    "Rules:\n"
    "1. requires_verification is always true. Output is flagged for "
    "manual review either way.\n"
    "2. For name2_suggestion, when the institution is well-known "
    "(e.g. Harvard Medical School, University of Florida) and the "
    "contact's department can be plausibly inferred from public "
    "knowledge, propose a SPECIFIC department-level guess (e.g. "
    "'Department of Neuroscience', 'Department of Genetics'). Use "
    "confidence='medium' for educated guesses, 'low' for shots in "
    "the dark, 'high' only when you are certain. A best-guess "
    "department is more useful than null.\n"
    "3. Strongly prefer 'Department of X' or 'Division of X' over "
    "'School of X' / 'College of X' / 'Faculty of X'. A faculty "
    "member at 'College of Medicine' is always inside a specific "
    "department. Only fall back to school/college/faculty when no "
    "plausible department guess exists.\n"
    "4. Return null for name2_suggestion only when the institution "
    "is unknown to you or the contact has no inferrable affiliation.\n"
    "5. Do NOT return any of name2..name5 equal to name1, equal to each "
    "other, or a parent of name1 (e.g. name1='Harvard Medical School' "
    "must not yield name2='Harvard University'). Name 2 is the "
    "department, and each slot below it is a narrower unit inside the "
    "one above — a division, then a lab or group. Return null for a "
    "slot with no such narrower unit rather than restating a broader "
    "one.\n"
    "6. No fabrication of institutions or invented people.\n"
    "7. NEVER put address content in a name field. The street, house "
    "number, postal code, and city provided as context are address "
    "fields — every name*_suggestion "
    "must never contain a street name, house number, postal/ZIP code, "
    "or a city/site string copied from the address. If you cannot infer "
    "a real organisation or department name, return null for that field."
)


# ---------------------------------------------------------------------------
# Person affiliation (Stage 2b) — discover a contact's institution + department
# ---------------------------------------------------------------------------
#
# Used ONLY when Name 1 held just a person's name (moved to Contact), so the
# record has a contact but no organisation. Reads web-search snippets and
# proposes the person's CURRENT employer/institution + department. The caller
# then confirms the institution against ROR in the record's country before
# accepting it — so this prompt must be GROUNDED (only what the snippets say)
# and must never guess to fill the field.
PERSON_AFFILIATION_SYSTEM_PROMPT = (
    "You identify the CURRENT primary employer/institution and department of a "
    "named person from web-search result snippets.\n"
    "\n"
    "Rules:\n"
    "1. Ground every answer in the provided snippets. If the snippets do not "
    "clearly tie THIS person (by full name) to an institution, return "
    "institution=null. Never guess from the name alone.\n"
    "2. institution = the organisation the person works at now (university, "
    "research institute, hospital, or company) — its full proper name, not an "
    "acronym.\n"
    "3. department = the person's sub-unit/department if a snippet states it; "
    "otherwise null.\n"
    "4. Match the person by full name. If the snippets are about a different "
    "person with a similar name, return institution=null.\n"
    "5. confidence: 'high' when a snippet explicitly names this person AND their "
    "institution together; 'medium' when the tie is strongly implied by one "
    "snippet; 'low' when uncertain or conflicting.\n"
    "6. Never output an address, street, city, or postal code in institution or "
    "department. These are name fields, not address fields.\n"
    "7. No fabrication. Prefer institution=null over a plausible guess.\n"
    "\n"
    "Return ONLY JSON: "
    '{"institution": string|null, "department": string|null, '
    '"confidence": "high"|"medium"|"low"}.'
)

PERSON_AFFILIATION_USER_PROMPT_TEMPLATE = (
    "Person: {contact}\n"
    "Known location (from the record): {location}\n"
    "\n"
    "Web search results:\n"
    "{results}\n"
    "\n"
    "Identify this person's current institution and department per the rules. "
    "Return the JSON object only."
)




# ---------------------------------------------------------------------------
# Prompt versions (Fix 10)
# ---------------------------------------------------------------------------
#
# A value produced by a model is not reproducible without the deployment, the
# temperature and the prompt it was produced from — and a prompt is edited far
# more often than a deployment is replaced. `prompt_version` is what the
# provenance log records; the prompt TEXT is never logged.
#
# The version is a declared major (`v1`) plus a short digest of the prompt pair
# that actually shipped. The declared part is for humans and moves when the
# prompt's intent changes; the digest moves on any edit at all, including one
# nobody thought was semantic, which is precisely the case that makes an old
# value irreproducible without anyone noticing.

import hashlib as _hashlib


def _digest(*parts: str) -> str:
    h = _hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:8]


def prompt_version(name: str, major: str, *parts: str) -> str:
    """``<name>/<major>:<digest>`` — the identifier recorded on an LLM write."""
    return f"{name}/{major}:{_digest(*parts)}"


OVERFLOW_CHECK_PROMPT_VERSION = prompt_version(
    "overflow_check", "v1",
    OVERFLOW_CHECK_SYSTEM_PROMPT, OVERFLOW_CHECK_USER_PROMPT_TEMPLATE,
)
TIER2A_PROMPT_VERSION = prompt_version(
    "tier2a_contact", "v1",
    TIER2A_SYSTEM_PROMPT, TIER2A_USER_PROMPT_TEMPLATE,
)
TIER2B_PROMPT_VERSION = prompt_version(
    "tier2b_dept", "v1",
    TIER2B_SYSTEM_PROMPT, TIER2B_USER_PROMPT_TEMPLATE,
)
TIER3_PROMPT_VERSION = prompt_version(
    "tier3_llm", "v1",
    TIER3_SYSTEM_PROMPT, TIER3_USER_PROMPT_TEMPLATE,
)
LAB_PARENT_PROMPT_VERSION = prompt_version(
    "lab_parent", "v1",
    LAB_PARENT_SYSTEM_PROMPT, LAB_PARENT_USER_PROMPT_TEMPLATE,
)
COMPANY_CANONICAL_PROMPT_VERSION = prompt_version(
    "company_canonical", "v1",
    COMPANY_CANONICAL_SYSTEM_PROMPT, COMPANY_CANONICAL_USER_PROMPT_TEMPLATE,
)
PERSON_AFFILIATION_PROMPT_VERSION = prompt_version(
    "person_affiliation", "v1",
    PERSON_AFFILIATION_SYSTEM_PROMPT, PERSON_AFFILIATION_USER_PROMPT_TEMPLATE,
)
TIER2_CANONICAL_PROMPT_VERSION = prompt_version(
    "tier2_canonical", "v1",
    TIER2_CANONICAL_SYSTEM_PROMPT, TIER2_CANONICAL_USER_PROMPT_TEMPLATE,
)
PAGE_READ_PROMPT_VERSION = prompt_version(
    # v2 — rule 7 (most complete stated form) and the [footer] slice.
    "page_read", "v2",
    PAGE_READ_SYSTEM_PROMPT, PAGE_READ_USER_PROMPT_TEMPLATE,
)
