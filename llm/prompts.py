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
    "5. Do NOT return name2_suggestion equal to name1, and do NOT "
    "return a parent of name1 (e.g. name1='Harvard Medical School' "
    "must not yield name2='Harvard University').\n"
    "6. No fabrication of institutions or invented people."
)
