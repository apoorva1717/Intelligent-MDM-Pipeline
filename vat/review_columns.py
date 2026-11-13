"""The reviewer's columns (2026-11-08): what a person needs to decide a row,
in plain codes that can be filtered, and one sentence saying why.

Written after the SAP record's own columns, instead of the technical ones
(``VAT_OUTPUT_COLUMNS=review``, the default; ``full`` adds every technical
column after them). Everything here is read from the lookup's result: no
call, no change to the decision.

* ``Name_Check`` — one value: SAME_NAME / SAP_NAME_PLUS_MORE / SIMILAR_NAME
  / DIFFERENT_NAME (the page's company name against SAP Name 1–4).
* ``Address_Check`` — one value: SAME_ADDRESS / OTHER_STREET_SAME_TOWN /
  OTHER_TOWN / NO_ADDRESS_ON_PAGE.
* ``Issue_Flags`` — only what the reviewer must check, beyond name and
  address (:data:`FLAG_ORDER`).
* ``Not_Found_Reason`` — why a row has no number.
* ``VAT_Why`` — one or two sentences built from the above by templates; no
  code in the text.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from vat.entity_match import ADDRESS_BLOCK_RE, clean_text, fold, issue_code
from vat.lookup import FoundResult

#: The reviewer's columns, in output order (the cost columns follow them).
REVIEW_COLUMNS: list[str] = [
    "VAT_Proposed",
    "VAT_Decision",
    "VAT_Found_Confidence",
    "VAT_Found_Match_Score",
    "VAT_Why",
    "Found_Name",
    "Found_Address",
    "Name_Check",
    "Address_Check",
    "Issue_Flags",
    "Not_Found_Reason",
    "Agent_Pass",
    "VIES",
    "VAT_Found_Source_URL",
    "VAT_Found_Source_Type",
    "VAT_Found_Source_Class",
    "VAT_Found_Page_Title",
    "VAT_Found_Evidence",
    "VAT_Found_Agent_Version",
    # Step AL: the number a Not found row's lookup withdrew (another company's).
    "VAT_Withdrawn_Number",
    "VAT_Withdrawn_Found_Name",
    "VAT_Withdrawn_Source_URL",
]
#: Read from the lookup's own columns, unchanged.
_SAME_AS_LOOKUP = ("VAT_Found_Confidence", "VAT_Found_Match_Score", "VAT_Found_Source_URL",
                   "VAT_Found_Source_Type", "VAT_Found_Source_Class", "VAT_Found_Page_Title",
                   "VAT_Found_Agent_Version")

DECISIONS = {"AUTO_ACCEPT": "Auto-accept", "MANUAL_REVIEW": "Review", "CONFLICT": "Conflict",
             "NOT_FOUND": "Not found"}

# ── Name_Check / Address_Check ──────────────────────────────────────────────
SAME_NAME, SAP_NAME_PLUS_MORE, SIMILAR_NAME, DIFFERENT_NAME = (
    "SAME_NAME", "SAP_NAME_PLUS_MORE", "SIMILAR_NAME", "DIFFERENT_NAME")
SAME_ADDRESS, OTHER_STREET_SAME_TOWN, OTHER_TOWN, NO_ADDRESS_ON_PAGE = (
    "SAME_ADDRESS", "OTHER_STREET_SAME_TOWN", "OTHER_TOWN", "NO_ADDRESS_ON_PAGE")
_ADDRESS = {"EXACT": SAME_ADDRESS, "SAME_TOWN": OTHER_STREET_SAME_TOWN, "DIFFERENT_TOWN": OTHER_TOWN,
            "NO_ADDRESS": NO_ADDRESS_ON_PAGE}

# ── Issue_Flags, in this order ──────────────────────────────────────────────
SEVERAL_NUMBERS_ON_PAGE = "SEVERAL_NUMBERS_ON_PAGE"
#: A Conflict whose page has one number: the model named none, the code took it.
NUMBER_NOT_CONFIRMED = "NUMBER_NOT_CONFIRMED"
NOT_CONFIRMED_WHY = ("The model did not confirm a number for this customer; the code took the only number on "
                     "the page. Check whose number it is.")
OTHER_SOURCE_OTHER_NUMBER = "OTHER_SOURCE_OTHER_NUMBER"
FROM_DIRECTORY = "FROM_DIRECTORY"
FROM_OLD_ARCHIVE = "FROM_OLD_ARCHIVE"
NUMBER_NOT_QUOTED = "NUMBER_NOT_QUOTED"
MAYBE_PARENT_COMPANY = "MAYBE_PARENT_COMPANY"
MAYBE_RENAMED_OR_SUCCESSOR = "MAYBE_RENAMED_OR_SUCCESSOR"
SISTER_COMPANY_ON_PAGE = "SISTER_COMPANY_ON_PAGE"
LEGAL_FORM_DIFFERS = "LEGAL_FORM_DIFFERS"
REGISTER_NO_DIFFERS = "REGISTER_NO_DIFFERS"
DEPARTMENT_OR_UNIT = "DEPARTMENT_OR_UNIT"
SITE_OR_PLANT = "SITE_OR_PLANT"
NAME_SHARED_BY_OTHERS = "NAME_SHARED_BY_OTHERS"
IN_LIQUIDATION = "IN_LIQUIDATION"
FLAG_ORDER = (
    IN_LIQUIDATION, SEVERAL_NUMBERS_ON_PAGE, NUMBER_NOT_CONFIRMED, OTHER_SOURCE_OTHER_NUMBER, FROM_DIRECTORY,
    FROM_OLD_ARCHIVE, NUMBER_NOT_QUOTED,
    MAYBE_PARENT_COMPANY, MAYBE_RENAMED_OR_SUCCESSOR, SISTER_COMPANY_ON_PAGE, LEGAL_FORM_DIFFERS,
    REGISTER_NO_DIFFERS, DEPARTMENT_OR_UNIT, SITE_OR_PLANT, NAME_SHARED_BY_OTHERS,
)
#: Lookup issue codes → reviewer flag.
_FROM_ISSUE = {
    "PARENT_ENTITY_CANDIDATE": MAYBE_PARENT_COMPANY, "PARENT_ENTITY": MAYBE_PARENT_COMPANY,
    "SUCCESSOR_CANDIDATE": MAYBE_RENAMED_OR_SUCCESSOR, "SISTER_ENTITY_ON_PAGE": SISTER_COMPANY_ON_PAGE,
    "LEGAL_FORM_DIFFERS": LEGAL_FORM_DIFFERS, "REGISTER_MISMATCH": REGISTER_NO_DIFFERS,
    "DEPARTMENT": DEPARTMENT_OR_UNIT, "SUB_ENTITY": DEPARTMENT_OR_UNIT, "BRANCH": SITE_OR_PLANT,
    "EXTRACTED_BY_PATTERN": NUMBER_NOT_QUOTED, "EVIDENCE_NOT_VERIFIED": NUMBER_NOT_QUOTED,
    "IN_LIQUIDATION": IN_LIQUIDATION,
}

# ── Not_Found_Reason ────────────────────────────────────────────────────────
_NOT_FOUND = {
    "NOT_FOUND:NO_OWN_SITE": "NO_WEBSITE_FOUND",
    "NOT_FOUND:NO_NUMBER_ON_OWN_SITE": "WEBSITE_HAS_NO_VAT_ID",
    "NOT_FOUND:OWN_SITE_SCRIPT_ONLY": "WEBSITE_NOT_READABLE",
    "NOT_FOUND:OWN_SITE_BLOCKED": "WEBSITE_BLOCKED_US",
    "NOT_FOUND:PARENT_ONLY": "ONLY_PARENT_SITE_FOUND",
    "NOT_FOUND:ENTITY_DEFUNCT": "PAGE_SAYS_COMPANY_CLOSED",
    "NOT_FOUND_NO_VAT_EXPECTED:STATED": "PAGE_SAYS_NO_VAT_ID",
    "NOT_FOUND_NO_VAT_EXPECTED:INFERRED": "PUBLIC_BODY_NO_VAT",
    "NOT_FOUND:INVALID_NUMBER": "INVALID_VAT_NUMBER",
    "NOT_FOUND:ONLY_OTHER_COMPANY_FOUND": "ONLY_OTHER_COMPANY_FOUND",
}
SEARCH_INCOMPLETE = "SEARCH_INCOMPLETE"
NO_NUMBER_FOUND = "NO_NUMBER_FOUND"
ONLY_OTHER_COMPANY_FOUND = "ONLY_OTHER_COMPANY_FOUND"
#: Why a search could not finish, from the lookup's flags (first match wins:
#: the lookup's own failure before the agent pass's budget stop).
_INCOMPLETE = (
    ("LLM_UNAVAILABLE", "MODEL_SERVICE_FAILED"),
    ("MALFORMED_OUTPUT", "MODEL_ANSWER_UNREADABLE"),
    ("CONTENT_FILTER", "CONTENT_FILTER_STOPPED"),
    ("FROZEN_CACHE_MISS", "NOT_IN_REPLAY"),
    ("BUDGET_EXCEEDED", "COST_CAP_REACHED"),
)

VIES_TEXT = {"VALID": "Valid", "INVALID": "Invalid", "PENDING": "Pending", "UNAVAILABLE": "Unavailable",
             "SERVICE_UNAVAILABLE": "Unavailable", "ERROR": "Unavailable", "NOT_CHECKED": "Not checked"}

#: The marker on VAT_Found_Evidence when no line was quoted.
AROUND_THE_NUMBER = "(text around the number) "


def _norm(vat: str | None) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(vat or "").upper())


_VAT_SHAPE_RE = re.compile(r"[A-Z]{2}[0-9A-Z]{8,12}")


def _is_vat_id(text: str | None) -> bool:
    """*text* is shaped like an EU VAT ID (an EU prefix, then 8–12 characters)."""
    from vat.normalize import VIES_PREFIXES

    v = _norm(text)
    return bool(_VAT_SHAPE_RE.fullmatch(v)) and v[:2] in VIES_PREFIXES


def proposed(r: FoundResult) -> str | None:
    """The proposed number in its standard form ("DE 196 867 425" as printed
    on a page → DE196867425), as SAP and VIES take it."""
    if not r.value:
        return None
    from vat.normalize import assess_format

    return assess_format(r.value, r.value[:2].upper()).normalized or r.value


def name_check(r: FoundResult) -> str | None:
    if not r.value or not r.name_match:
        return None
    if r.name_match == "EXACT":
        return SAME_NAME
    if r.name_match == "PARTIAL":
        return SAP_NAME_PLUS_MORE if any(issue_code(i) == "NAME_CONTAINED" for i in r.issues) else SIMILAR_NAME
    return DIFFERENT_NAME


def issue_flags(r: FoundResult) -> list[str]:
    """The reviewer's flags for a found number, in :data:`FLAG_ORDER`."""
    if not r.value:
        return []
    flags: set[str] = set()
    for i in r.all_issues:
        if issue_code(i) in _FROM_ISSUE:
            flags.add(_FROM_ISSUE[issue_code(i)])
    value = _norm(r.value)
    ambiguous_alone = False
    for f in r.flags:
        code, _, rest = f.partition(":")
        if code in ("OTHER_VAT_IDS_ON_PAGE", "AMBIGUOUS_SAME_NAME"):
            # Only another VAT ID: not the proposed number again (spaced),
            # nor a tax or register number the reader listed (2026-11-12:
            # 16 of 20 sampled "several numbers" pages showed one number).
            if any(_is_vat_id(o) and _norm(o) != value for o in rest.split(",")):
                flags.add(SEVERAL_NUMBERS_ON_PAGE)
            elif code == "AMBIGUOUS_SAME_NAME":
                ambiguous_alone = True
        elif code == "CONFLICTING_SOURCES":
            flags.add(OTHER_SOURCE_OTHER_NUMBER)
        elif code in ("QUOTE_NOT_IN_PAGE", "ID_NOT_IN_PAGE"):
            flags.add(NUMBER_NOT_QUOTED)
        elif code == "ARCHIVE_SOURCE":
            flags.add(FROM_OLD_ARCHIVE)
        elif code in ("GENERIC_NAME", "NAME_ABBREVIATED"):
            flags.add(NAME_SHARED_BY_OTHERS)
    if ambiguous_alone and SEVERAL_NUMBERS_ON_PAGE not in flags:
        # A Conflict with no second number: the reader named none and the
        # code took the page's only one.
        flags.add(NUMBER_NOT_CONFIRMED)
    if r.source_type == "WEB_ARCHIVE":
        flags.add(FROM_OLD_ARCHIVE)
    if r.source_class == "C" and FROM_OLD_ARCHIVE not in flags:
        flags.add(FROM_DIRECTORY)
    if not (r.evidence or "").strip():
        flags.add(NUMBER_NOT_QUOTED)
    return [f for f in FLAG_ORDER if f in flags]


def not_found_reason(r: FoundResult) -> str | None:
    if r.value or r.status == "NOT_RUN":
        return None
    if r.status in _NOT_FOUND:
        return _NOT_FOUND[r.status]
    if r.status.startswith("LOOKUP_INCOMPLETE"):
        if "HTTP" in r.status or "ALL_OWN_HOSTS_BLOCKED" in r.flags:
            return "WEBSITE_BLOCKED_US"
        codes = {f.partition(":")[0] for f in r.flags} | {r.status.partition(":")[2]}
        return next((reason for code, reason in _INCOMPLETE if code in codes), SEARCH_INCOMPLETE)
    return NO_NUMBER_FOUND


#: The number a lookup rejected (``vat.lookup.reject_invalid_number``) and
#: why, for VAT_Why only; not a column.
_REJECTED = "_rejected"
_INVALID_WHY = {
    "InvalidChecksum": "wrong check digit", "InvalidLength": "wrong length",
    "PREFIX_COUNTRY_MISMATCH": "another country's number", "NON_EU_PREFIX": "not an EU VAT ID",
}


#: The number a lookup withdrew (``vat.lookup.withdraw``), for the
#: VAT_Withdrawn_* columns and VAT_Why; not a column itself.
_WITHDRAWN = "_withdrawn"


def withdrawn(r: FoundResult) -> dict[str, Any] | None:
    """``usage["withdrawn"]`` of a row with no number, or None."""
    w = (r.usage or {}).get("withdrawn")
    return w if isinstance(w, dict) and w.get("number") and not r.value else None


def page_town(address: str | None) -> str | None:
    """The town of the first ``<postcode> <Town>`` on an address or page text."""
    m = ADDRESS_BLOCK_RE.search(address or "")
    if not m:
        return None
    rest = _ADDRESS_END_RE.sub("", " " + m.group(2)).strip()
    rest = re.split(r"[,;|/·(]|\s{2,}|\s-\s", rest)[0].strip(" .-")
    return rest or None


def rejected_number(r: FoundResult) -> tuple[str, str] | None:
    """``(number, why)`` from the lookup's ``INVALID_NUMBER:<number>:<why>`` flag."""
    for f in r.flags:
        code, _, rest = f.partition(":")
        if code == "INVALID_NUMBER":
            number, _, reason = rest.partition(":")
            return number, reason
    return None


#: Agent_Pass: what the agent pass (Step AG, vat/lookup_agent.py) did on the row.
AGENT_FOUND, AGENT_NOTHING, AGENT_CAP = "Found", "Tried, nothing found", "Skipped (cost cap)"


def agent_pass(r: FoundResult) -> str | None:
    """Found (the proposal is the agent's) / Tried, nothing found / Skipped
    (cost cap); blank when the pass did not run on the row (off, or the
    lookup already had a number or expects none)."""
    if "hybrid_status" in (r.usage or {}):
        return AGENT_FOUND
    if "AGENT_SKIPPED_BUDGET" in r.flags:
        return AGENT_CAP
    if "AGENT_TRIED" in r.flags:
        return AGENT_NOTHING
    return None


def found_address(r: FoundResult, postal_code: str | None, city: str | None) -> str | None:
    """The address the reader copied from the page; else the page's address
    block with SAP's postcode or town (what the address check found), else
    its first address block."""
    if not r.value:
        return None
    if r.published_address:
        return r.published_address
    text = r.address_text or ""
    blocks = [" ".join(text[max(0, m.start() - 60):m.end()].split()) for m in ADDRESS_BLOCK_RE.finditer(text)]
    want = [w for w in (str(postal_code or "").strip(), fold(city)) if w]
    for b in blocks:
        if any(w in b or w in fold(b) for w in want):
            return _from_line_start(b)
    return _from_line_start(blocks[0]) if blocks else None


#: Where an address block ends: the contact lines after it.
_ADDRESS_END_RE = re.compile(
    r"\s(?:Tel(?:efon)?|Fon|Fax|Telefax|Phone|E-?Mail|Internet|Homepage|Web|www\.|Geschäftsführ\w*|Vorstand|"
    r"Registergericht|Amtsgericht|Handelsregister|USt)\b.*$|\s\+\d.*$", re.I)
#: Words before the address that are the page's, not the address's.
_ADDRESS_LEAD_RE = re.compile(
    r"^.*\b(?:Impressum|Imprint|\w*anschrift|\w*adresse|Address|Kontakt|Contact)\b[:\s]*", re.I)
#: A leading word that is not the start of a name or street: lower case,
#: a bracket, or a fragment cut off the text before.
_ADDRESS_FRAGMENT_RE = re.compile(r"^(?:[a-zäöüß][^\s]*|\([^)]*\)|[^\sA-ZÄÖÜ0-9][^\s]*)\s+")


def _from_line_start(block: str) -> str:
    """The address block as a reviewer reads it: no word cut at the front,
    no heading before it, no phone / e-mail / register lines after it."""
    block = _ADDRESS_LEAD_RE.sub("", block)
    while _ADDRESS_FRAGMENT_RE.match(block):
        block = _ADDRESS_FRAGMENT_RE.sub("", block, count=1)
    block = _ADDRESS_END_RE.sub("", block)
    return block.strip(" |·-:…,;")


def evidence(r: FoundResult) -> str | None:
    if not r.value:
        return None
    if (r.evidence or "").strip():
        return r.evidence
    snippet = (r.usage or {}).get("evidence_snippet") or ((r.usage or {}).get("hybrid_usage") or {}).get(
        "evidence_snippet")
    return AROUND_THE_NUMBER + snippet if snippet else None


def review_columns(r: FoundResult | None, sap_name: str | None = None, postal_code: str | None = None,
                   city: str | None = None) -> dict[str, Any]:
    """The :data:`REVIEW_COLUMNS` of one row; blank for a row not looked up."""
    if r is None:
        return dict.fromkeys(REVIEW_COLUMNS)
    lookup = r.columns()
    row = {
        "VAT_Proposed": proposed(r),
        "VAT_Decision": DECISIONS.get(r.decision or "", r.decision),
        **{c: lookup.get(c) for c in _SAME_AS_LOOKUP},
        "Found_Name": clean_text(r.attributed_to) or None if r.value else None,
        "Found_Address": clean_text(found_address(r, postal_code, city)) or None,
        "Name_Check": name_check(r),
        "Address_Check": _ADDRESS.get(r.address_match or "") if r.value else None,
        "Issue_Flags": "; ".join(issue_flags(r)) or None,
        "Not_Found_Reason": not_found_reason(r),
        "Agent_Pass": agent_pass(r),
        "VIES": VIES_TEXT.get(r.validation_status or "") if r.value else None,
        "VAT_Found_Evidence": clean_text(evidence(r)) or None,
        _REJECTED: rejected_number(r),
        _WITHDRAWN: withdrawn(r),
    }
    if row[_WITHDRAWN]:
        w = row[_WITHDRAWN]
        row.update(VAT_Withdrawn_Number=w.get("number"), VAT_Withdrawn_Found_Name=clean_text(w.get("found_name")) or None,
                   VAT_Withdrawn_Source_URL=w.get("source_url"))
    row["VAT_Why"] = why(row, sap_name, city)
    return {c: row.get(c) for c in REVIEW_COLUMNS}


# ── VAT_Why ─────────────────────────────────────────────────────────────────

_NOT_FOUND_TEXT = {
    "NO_WEBSITE_FOUND": "No website of the organisation was found{town}; only directories or unrelated pages.",
    "WEBSITE_HAS_NO_VAT_ID": "The organisation's own legal notice was read; it shows no VAT ID.",
    "WEBSITE_NOT_READABLE": "The organisation's website could not be read (it only loads with JavaScript).",
    "WEBSITE_BLOCKED_US": "The organisation's website refused automated access.",
    "ONLY_PARENT_SITE_FOUND": "Only the parent organisation's site was found; it shows no number for this record.",
    "PAGE_SAYS_COMPANY_CLOSED": "A page says the company is closed, merged or in liquidation.",
    "PAGE_SAYS_NO_VAT_ID": "The company's page states it has no VAT ID.",
    "PUBLIC_BODY_NO_VAT": "A public body; no page states a VAT ID (assumed to have none).",
    ONLY_OTHER_COMPANY_FOUND: "The only number found belongs to another company; no page gives a number "
                              "to this customer.",
    "INVALID_VAT_NUMBER": "The number found{rejected} is not a valid VAT ID for this record; "
                          "get it from an invoice or the partner.",
    "MODEL_SERVICE_FAILED": "The model service rejected or failed the call (the server log has Azure's message); "
                            "check the API settings and run again.",
    "MODEL_ANSWER_UNREADABLE": "The model's answer could not be read; run again.",
    "CONTENT_FILTER_STOPPED": "Azure's content filter stopped the answer twice; check by hand.",
    "NOT_IN_REPLAY": "This run only replays recorded answers and had none for this record; run it live.",
    "COST_CAP_REACHED": "The run's cost cap was reached before this record; raise the cap and run again.",
    SEARCH_INCOMPLETE: "The search could not finish; run again.",
    NO_NUMBER_FOUND: "No VAT ID was found.",
}


#: Small words of German town names, lower case after title-casing.
_TOWN_SMALL = frozenset({"am", "im", "an", "der", "die", "dem", "den", "bei", "ob", "vor", "in", "auf", "unter",
                         "v", "d", "h", "i"})


def town(name: str) -> str:
    """SAP's town (often in capitals) as written: "FRANKFURT AM MAIN" → "Frankfurt am Main"."""
    if not name.isupper():
        return name
    words = re.split(r"(\W+)", name.title())
    return "".join(w.lower() if i and w.lower() in _TOWN_SMALL else w for i, w in enumerate(words))


def _q(text: Any) -> str:
    return f"'{text}'"


def _host(url: str | None) -> str:
    return (urlparse(url or "").hostname or "").removeprefix("www.")


def why(row: dict[str, Any], sap_name: str | None = None, sap_town: str | None = None) -> str | None:
    """One or two plain sentences for a row, from its review columns: the
    most important finding first. Never a code, never an empty placeholder."""
    decision = row.get("VAT_Decision")
    if not row.get("VAT_Proposed"):
        reason = row.get("Not_Found_Reason")
        if not reason:
            return None
        where = f" in {town(sap_town)}" if sap_town else ""
        w = row.get(_WITHDRAWN)
        if reason == ONLY_OTHER_COMPANY_FOUND and w:
            holder = _q(w.get("found_name")) if w.get("found_name") else "another company"
            if w.get("parent"):
                holder = f"the parent or group company {holder}"
            place = page_town(w.get("found_address")) or page_town(w.get("address_text"))
            return (f"The only number found belongs to {holder}{f' in {town(place)}' if place else ''}; "
                    f"no page gives a number to {_q(sap_name) if sap_name else 'this customer'}.")
        rejected = row.get(_REJECTED)
        rejected = f" ({rejected[0]}: {_INVALID_WHY.get(rejected[1], 'wrong format')})" if rejected else ""
        return _NOT_FOUND_TEXT.get(reason, _NOT_FOUND_TEXT[NO_NUMBER_FOUND]).format(town=where, rejected=rejected)
    flags = set((row.get("Issue_Flags") or "").split("; ")) - {""}
    found = row.get("Found_Name")
    sap = sap_name or "the SAP name"
    name, address = row.get("Name_Check"), row.get("Address_Check")
    out: list[str] = []
    if row.get("VIES") == "Invalid":
        out.append("VIES says this number is not valid.")
    if IN_LIQUIDATION in flags:
        out.append(f"The page names {_q(found) if found else 'the company'} as in liquidation; "
                   "check that the customer is still active.")
    if MAYBE_PARENT_COMPANY in flags:
        out.append(f"The page belongs to {_q(found) if found else 'another company'}, possibly the parent; "
                   f"check whether {_q(sap)} has its own number.")
    if MAYBE_RENAMED_OR_SUCCESSOR in flags:
        out.append(f"The page names {_q(found) if found else 'another name'}: possibly renamed or a successor.")
    if SEVERAL_NUMBERS_ON_PAGE in flags:
        out.append(f"The page lists several VAT IDs; check that the proposed one belongs to {_q(sap)}.")
    if NUMBER_NOT_CONFIRMED in flags:
        out.append(NOT_CONFIRMED_WHY)
    if OTHER_SOURCE_OTHER_NUMBER in flags:
        out.append("Another source gives a different number for this company.")
    if SISTER_COMPANY_ON_PAGE in flags:
        out.append(f"The page also names a sister company; check that the number belongs to {_q(sap)}.")
    if FROM_DIRECTORY in flags:
        src = _host(row.get("VAT_Found_Source_URL"))
        out.append(f"The number comes from a business directory{f' ({src})' if src else ''}, "
                   "not the company's own site.")
    if FROM_OLD_ARCHIVE in flags:
        out.append("The number comes from an old archived copy of the website; check it is still current.")
    if NUMBER_NOT_QUOTED in flags:
        out.append("The number was taken from the page text without a quoted line; check it on the page.")
    if REGISTER_NO_DIFFERS in flags:
        out.append("The register number (HRB) on the page differs from SAP's.")
    if LEGAL_FORM_DIFFERS in flags:
        out.append(f"The page names {_q(found)} with another legal form than SAP." if found
                   else "The legal form on the page differs from SAP's.")
    if name == DIFFERENT_NAME and found and MAYBE_PARENT_COMPANY not in flags:
        out.append(f"The page names {_q(found)}, not {_q(sap)}.")
    elif name == SIMILAR_NAME and found and not flags & {MAYBE_PARENT_COMPANY, LEGAL_FORM_DIFFERS}:
        out.append(f"The page names {_q(found)}, only partly like {_q(sap)}.")
    if address == OTHER_TOWN:
        site = SITE_OR_PLANT in flags
        out.append(f"The address on the page is in another town than SAP's{f' ({town(sap_town)})' if sap_town else ''}"
                   + ("; SAP holds a site, the number is the head office's." if site
                      else ": head office, or another company with the same name?"))
    elif address == NO_ADDRESS_ON_PAGE:
        out.append("The page shows no address.")
    elif address == OTHER_STREET_SAME_TOWN and NAME_SHARED_BY_OTHERS in flags:
        out.append("The name is shared by other companies and the street differs from SAP's.")
    if out:
        return " ".join(out[:2])
    # Nothing to check: say what matched.
    own = "the organisation's own website" if row.get("VAT_Found_Source_Class") == "A" else "the source page"
    match = (f"the page name {_q(found)} contains the SAP name" if name == SAP_NAME_PLUS_MORE
             else "name matches" if name == SAME_NAME else "name checked")
    where = {SAME_ADDRESS: "address matches", OTHER_STREET_SAME_TOWN: "same town"}.get(address, "")
    text = f"Number from {own}; {match}{', ' + where if where else ''}."
    if DEPARTMENT_OR_UNIT in flags:
        text += " The SAP record is a department or unit; the organisation's central number applies."
    elif SITE_OR_PLANT in flags:
        text += " The SAP record is a site; the number is the company's."
    elif decision == "Auto-accept" and row.get("VIES") == "Pending":
        text += " Goes to SAP once VIES confirms it."
    return text


# ── The VIES re-check on a written row ──────────────────────────────────────

#: Reviewer columns → the codes the review tier reads (vat.lookup.review_tier).
_TIER_CODES = {
    SIMILAR_NAME: "NAME_PARTIAL", DIFFERENT_NAME: "NAME_PARTIAL", OTHER_TOWN: "ADDRESS_MISMATCH",
    NO_ADDRESS_ON_PAGE: "ADDRESS_NOT_ON_PAGE", FROM_DIRECTORY: "SECONDARY_SOURCE",
    SEVERAL_NUMBERS_ON_PAGE: "OTHER_VAT_IDS_ON_PAGE", NUMBER_NOT_QUOTED: "EXTRACTED_BY_PATTERN",
    MAYBE_PARENT_COMPANY: "PARENT_ENTITY_CANDIDATE", MAYBE_RENAMED_OR_SUCCESSOR: "SUCCESSOR_CANDIDATE",
    LEGAL_FORM_DIFFERS: "LEGAL_FORM_DIFFERS", IN_LIQUIDATION: "IN_LIQUIDATION",
}
_TIER_NAME = {SAME_NAME: "EXACT", SAP_NAME_PLUS_MORE: "PARTIAL", SIMILAR_NAME: "PARTIAL", DIFFERENT_NAME: "MISMATCH"}
_TIER_ADDRESS = {v: k for k, v in _ADDRESS.items()}


def apply_vies(row: dict[str, Any], answers: dict[str, tuple[str, str | None]], sap_name: str | None = None,
               sap_town: str | None = None) -> bool:
    """VIES's later answer on a written review row, in place: ``VIES``,
    ``VAT_Decision`` (INVALID: Review, a Conflict stays), the confidence and
    ``VAT_Why``. Only rows still Pending / Unavailable. True when it changed."""
    from vat.lookup import CONF_LOW, review_tier
    from vat.normalize import assess_format

    value = row.get("VAT_Proposed")
    if not value or row.get("VIES") not in ("Pending", "Unavailable"):
        return False
    answer = answers.get(assess_format(value, "DE").normalized or value)
    if not answer:
        return False
    status = answer[0]
    row["VIES"] = VIES_TEXT.get(status, status)
    if status == "VALID":
        if row.get("VAT_Decision") == "Review":
            flags = set((row.get("Issue_Flags") or "").split("; ")) - {""}
            codes = {_TIER_CODES[c] for c in (*flags, row.get("Name_Check"), row.get("Address_Check"))
                     if c in _TIER_CODES}
            evidence_text = row.get("VAT_Found_Evidence") or ""
            row["VAT_Found_Confidence"] = review_tier(
                _TIER_NAME.get(row.get("Name_Check") or ""), _TIER_ADDRESS.get(row.get("Address_Check") or ""),
                row.get("VAT_Found_Source_Class"), codes,
                None if evidence_text.startswith(AROUND_THE_NUMBER) else evidence_text)
    else:
        if row.get("VAT_Decision") != "Conflict":
            row["VAT_Decision"] = "Review"
            row["VAT_Found_Confidence"] = CONF_LOW
    row["VAT_Why"] = why(row, sap_name, sap_town)
    return True
