"""All LLM prompt strings as module-level constants.

Centralised here so they can be versioned, reviewed, and tested independently.
"""

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
    "Rules: extract only what is on the page.\n"
    "If person not on page: person_found=false, rest null.\n"
    "official_dept and official_group verbatim from page.\n"
    "Use JSON null (not the string \"null\") for missing fields."
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
