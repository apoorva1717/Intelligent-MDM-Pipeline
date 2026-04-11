"""All LLM prompt strings as module-level constants.

Centralised here so they can be versioned, reviewed, and tested independently.
"""

# ---------------------------------------------------------------------------
# UC 0 — Name1 overflow into Name2 detection
# ---------------------------------------------------------------------------

OVERFLOW_CHECK_SYSTEM_PROMPT = (
    "You detect whether two adjacent customer-master name fields "
    "read as one continuous organisation name split across the "
    "fields, or as two separate entities. Return valid JSON only."
)

OVERFLOW_CHECK_USER_PROMPT_TEMPLATE = (
    "Name 1: {name1}\n"
    "Name 2: {name2}\n\n"
    "Read these two fields together as if they were the full name of "
    "one organisation. Does the concatenation 'Name 1 + Name 2' form "
    "a single continuous organisation name (an overflow), or do "
    "Name 1 and Name 2 describe two distinct entities (e.g. an "
    "institution + a department)?\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "is_overflow": true | false,\n'
    '  "confidence": "high" | "medium" | "low",\n'
    '  "reasoning": "str"\n'
    "}}\n\n"
    "Rules:\n"
    "1. is_overflow=true only when Name 1 + Name 2 reads naturally as "
    "ONE organisation name — e.g. 'Adams Air' + 'Hydraulics Inc' → "
    "'Adams Air Hydraulics Inc'.\n"
    "2. is_overflow=false when Name 2 is a department, division, "
    "research group, lab, contact person, or any standalone unit "
    "within Name 1.\n"
    "3. When in doubt, prefer false. The goal is to surface likely "
    "overflows, not to flag every case with a shared word.\n"
    "4. Legal suffixes (Inc, Ltd, LLC, Corp, Co, GmbH, AG) appearing "
    "in Name 2 with no department qualifier are a strong overflow "
    "signal."
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
    "3. Prefer a full unit construction ('Department of X', "
    "'Division of X', 'School of X', 'Institute of X'). Expand any "
    "subdomain abbreviation to the institution's actual canonical "
    "department wording.\n"
    "4. Reject generic role labels such as 'Research', 'Admin', "
    "'Staff', 'Faculty', 'Team', or 'Office'. They describe what the "
    "person does, not the unit they belong to. If the body contains "
    "only a role label, derive the unit from the URL host instead.\n"
    "5. Do not return a bare subject word alone ('Anesthesia', "
    "'Chemistry') and do not return a job title ('Professor of X').\n"
    "6. official_group may be set verbatim from the body when a "
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
    "2. Return null if you are not sure.\n"
    "3. Do not invent companies. Do not resolve acronyms you do not "
    "recognise.\n"
    "4. confidence=high means you are certain of the exact wording."
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
    "Contact: {contact}\n"
    "Address: {street}, {city}, {state} {zip}, {country}\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "name1_suggestion": "str or null",\n'
    '  "name2_suggestion": "str or null",\n'
    '  "name3_suggestion": "str or null",\n'
    '  "confidence": "high|medium|low",\n'
    '  "reasoning": "str",\n'
    '  "requires_verification": true\n'
    "}}\n"
    "Rules: requires_verification always true.\n"
    "Return null if not confident. No fabrication."
)
