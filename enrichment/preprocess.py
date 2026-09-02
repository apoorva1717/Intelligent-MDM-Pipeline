"""Deterministic preprocessing — UC 6, 7, 8, 9, 10, 11, 12, 14, 15.

Runs BEFORE any network/LLM call and is entirely pattern-based. The
preprocessing stage cleans up name fields by pulling out content that
clearly belongs elsewhere (emails, addresses, contact names, AP
references, c/o + ATTN routing) and moving it to the correct field.
No SerpAPI, no ROR, no LLM on the hot path. An optional LLM person-
vs-org classifier is only called when a name-like token pattern is
found with no title prefix and no other signals.

The shape of the returned dict mirrors the mutable subset of an
``EnrichmentRecord``:
    {
      "name1": ..., "name2": ..., ... "name5": ...,
      "care_of": ..., "contact": ..., "email": ...,
      "street1": ..., "street2": ..., "street3": ...,
    }
plus bookkeeping:
    {
      "use_cases": [6, 7, 8, 9, 15, ...],
      "flags": [("reason string"), ...],
      "trigger_dept_lookup": True if UC 15 Case A fired,
    }
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from enrichment import dept_block
from enrichment.locality import (
    CONTRADICTED,
    compare_locality,
    split_site_suffix,
)
from utils.domain_resolver import EMAIL_SEPARATOR, split_emails
from utils.name_slots import (
    ADJACENT_NAME_PAIRS,
    DEPT_SLOTS,
    NAME_SLOT_LABELS,
    NAME_SLOTS,
)
from utils.text_utils import (
    PARENT_ORG_ACRONYMS,
    canonicalise_unit_name,
    expand_abbreviations,
    is_granular_unit,
    is_logistics_location,
    is_unit_construction,
    looks_like_research_institution,
    smart_title_case,
    strip_parentheticals,
)

logger = logging.getLogger(__name__)

# The address block, in slot order. Name slots come from ``utils.name_slots``
# so every name rule shares one definition; the street block is local because
# nothing outside preprocessing walks it.
STREET_SLOTS: tuple[str, ...] = (
    "street1", "street2", "street3", "street4", "street5",
)


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

@dataclass
class PreprocessResult:
    name1: str | None = None
    name2: str | None = None
    name3: str | None = None
    name4: str | None = None
    name5: str | None = None
    care_of: str | None = None
    contact: str | None = None
    email: str | None = None
    street1: str | None = None
    street2: str | None = None
    street3: str | None = None
    street4: str | None = None
    street5: str | None = None
    # A named building pulled out of a name field (e.g. "Neil Armstrong
    # Operations and Checkout Building"). Routed to the Building output by
    # the orchestrator — it is a physical location, not a department.
    building: str | None = None
    use_cases: list[int] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    # Names where UC 11 normalised a DBA variant to "DBA". Downstream
    # tiers must not strip the marker — if an LLM canonicalisation drops
    # it, finalise() re-prepends "DBA " to the enriched value.
    dba_fields: set[str] = field(default_factory=set)
    # UC 15 (Case A): preprocessing extracted a person from a c/o or
    # ATTN prefix on Name 2 and the field is now empty. Tier 2A will
    # naturally fire on (name2=null, contact set, domain), but we
    # surface the flag explicitly so callers / tests can assert it.
    trigger_dept_lookup: bool = False
    # A person name was extracted from Name 1 (the institution slot), leaving
    # Name 1 empty. The orchestrator uses this to run a person-affiliation
    # web lookup so the discovered institution + department can be fetched.
    name1_was_person: bool = False
    # Name slots holding a value one of the street→name routers fetched out of
    # a STREET field, rather than a value supplied in the name block. Resolved
    # from `street_sourced_values` at the very end of preprocess_record, once
    # UC 14 packing and the UC 12 dedupe have settled which slot holds what.
    names_from_street: set[str] = field(default_factory=set)
    # The values those routers moved, recorded as each one fires. Values and
    # not slot names, because the packing moves a value between slots — a slot
    # recorded at routing time would name the wrong value by the end.
    street_sourced_values: list[str] = field(default_factory=list)
    #: Values UC 16 split out of Name 1, recorded as the split fires. Values
    #: and not slot names, for the same reason `street_sourced_values` is:
    #: UC 14's pack moves a value between slots after the split, and a slot
    #: recorded at split time would name the wrong value by the end.
    split_values: list[str] = field(default_factory=list)
    #: Resolved at the very end, alongside `names_from_street`: slot ->
    #: `dept_block` origin, for the slots whose value did not simply stay
    #: where the record put it. Empty for a record that arrived intact.
    slot_origins: dict[str, str] = field(default_factory=dict)
    # A site qualifier taken off a name slot whose place CONTRADICTS the
    # record's own city/state — "Veracyte, Inc. - South San Francisco, CA" on
    # a record addressed in Pine Brook, NJ. The qualifier comes off the name
    # either way (it is not part of the organisation's name, and carrying it
    # there is why no registry matches), but the two places are a question
    # only a steward can settle, so the finding is handed up rather than
    # dropped with the text. Rendered as `name-states-another-site`; the first
    # such conflict wins, and it reads "Name 2 states X; record says Y".
    site_conflict: str | None = None

    def note(self, uc: int, reason: str) -> None:
        if uc not in self.use_cases:
            self.use_cases.append(uc)
        self.flags.append(reason)

    def add_email(self, address: str) -> str:
        """Record *address* in ``email``, keeping every address already there.

        Returns ``"new"`` (the field was empty), ``"duplicate"`` (this address
        is already on the record) or ``"conflict"`` (a DIFFERENT address was
        already captured) — the caller raises ``email-conflict`` on the last.

        A conflict APPENDS, and that is the whole point of this method. Both
        addresses are the record's own and the pipeline has no basis for
        choosing between them: "USE6-INVOICES@SHELL.COM" in a name column and
        "email invoices to: USG5-Invoices@Shell.com" in a street line are two
        routing addresses one Shell record carries, not one right answer and
        one mistake. Keeping the first and abandoning the second lost the
        second — left in a Name column, where no consumer looks for an email,
        or (UC 15) discarded with the slot. Both ship, in the column that
        means "email", and the flag asks a steward which one the mail is for.

        ``utils.domain_resolver`` reads the column back the same way, so a
        second address does not change what any domain or affiliation lookup
        resolves — those take the first (:func:`~utils.domain_resolver.
        first_email`).
        """
        candidate = (address or "").strip()
        if not candidate:
            return "empty"
        existing = split_emails(self.email)
        if not existing:
            # A non-blank field holding something that does not parse as an
            # address is still what the record states; it is not overwritten.
            raw = (self.email or "").strip()
            existing = [raw] if raw else []
        if not existing:
            self.email = candidate
            return "new"
        if any(e.lower() == candidate.lower() for e in existing):
            return "duplicate"
        self.email = EMAIL_SEPARATOR.join([*existing, candidate])
        return "conflict"


# ---------------------------------------------------------------------------
# UC 6 — Accounts Payable normalisation
# ---------------------------------------------------------------------------

_AP_PATTERNS = [
    re.compile(r"\baccounts?\s+payable\b", re.IGNORECASE),
    re.compile(r"\baccts?\.?\s+payable\b", re.IGNORECASE),
    re.compile(r"\bacct\.?\s+payable\b", re.IGNORECASE),
    re.compile(r"\ba\s*/\s*p\b", re.IGNORECASE),
    # "AP Invoice", "AP Dept", "A/P Dept", "AP Department", "AP Div", "AP Division"
    re.compile(r"\bap\b(?:\s+invoice|\s+dept|\s+department|\s+div|\s+division)", re.IGNORECASE),
    re.compile(r"\baccounts?\s+pay\b", re.IGNORECASE),
    # "Acct Pay", "Accts Pay", "Acct. Pay" — the clipped SAP spelling.
    re.compile(r"\baccts?\.?\s+pay\b", re.IGNORECASE),
]


def _is_ap_reference(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _AP_PATTERNS)


#: A segment that is the accounts-payable desk and nothing else. A bare "AP"
#: is too ambiguous to match anywhere in a name ("AP Moller", "AP Chemicals"),
#: but a delimited segment of its own, sitting after a real organisation
#: name, is only ever the desk.
_BARE_AP_SEGMENTS = frozenset({"ap", "a/p", "a.p.", "acctpay", "acctspay"})


def _segment_is_ap(segment: str) -> bool:
    """True when a single delimited segment names the accounts-payable desk."""
    stripped = segment.strip().strip(".").lower()
    return stripped in _BARE_AP_SEGMENTS or _is_ap_reference(segment)


# Delimiters that separate an organisation from a trailing administrative desk
# inside one field: "McLaren HealthCare Corp/Acct Pay", "Acme Corp - A/P".
# A hyphen counts only with whitespace on both sides, so a hyphenated name
# ("Coca-Cola") is never a split point.
_AP_DELIM_RE = re.compile(r"\s*[|,;]\s*|\s+[-\u2013\u2014]\s+|\s*/\s*")
#: A slash between two single letters is the "A/P" abbreviation, not a
#: delimiter — "Acme Corp/A/P" splits once, before the "A/P".
_ABBREV_SLASH_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]\s*/\s*[A-Za-z](?![A-Za-z])")


def _trailing_ap_phrase(text: str) -> str | None:
    """The organisation when *text* ends with an undelimited AP desk.

    ``"Wyss Inst Accounts Payable"`` → ``"Wyss Inst"``. The delimited form is
    :func:`_split_ap_suffix`'s job; this is the same value written without the
    separator, which SAP records routinely are.

    Why it matters: `_is_ap_reference` matches anywhere in the field, and the
    caller used to replace the WHOLE field on a match. For a value that is
    only a desk ("Accounts Payable Department") that is right. For a value
    that names an organisation AND a desk it threw the organisation away —
    "Wyss Inst Accounts Payable" shipped as "Accounts Payable", and the record
    lost the only name it had. Returns None when the remainder is not a
    plausible organisation, so a desk-only value still flattens as before.
    """
    if not text or not text.strip():
        return None
    best: int | None = None
    for pattern in _AP_PATTERNS:
        for match in pattern.finditer(text):
            # Only a TRAILING desk. An AP phrase in the middle is part of
            # whatever the field is really saying, and cutting there would
            # leave a fragment.
            if text[match.end():].strip(" .,-/|;"):
                continue
            best = match.start() if best is None else min(best, match.start())
    if best is None:
        return None
    remainder = text[:best].strip(" .,-/|;")
    if not remainder:
        return None            # the value was the desk and nothing else
    if _is_ap_reference(remainder) or _segment_is_ap(remainder):
        return None            # still all desk after the cut
    # The remainder has to read as an organisation, or the split trades one
    # loss for another. "LSG Accts Payable" leaves "LSG" — a three-letter
    # internal code that names no customer, and the record is better served by
    # the desk it really states than by a fragment of one. Two tokens, or one
    # long enough to be a name, is the line: "Wyss Inst" and "Acme Corp" pass,
    # a bare code does not.
    words = re.findall(r"[A-Za-z0-9&.]+", remainder)
    if not words or not re.search(r"[A-Za-z]{2,}", remainder):
        return None
    if len(words) < 2 and len(re.sub(r"[^A-Za-z]", "", remainder)) < 5:
        return None
    return remainder


def _split_ap_suffix(text: str) -> str | None:
    """Return the organisation when *text* is "<organisation><delim><AP desk>".

    ``"McLaren HealthCare Corp/Acct Pay"`` → ``"McLaren HealthCare Corp"``,
    the caller then putting the desk in its own name slot. Returns None when
    the value is an AP reference through and through ("Accounts Payable
    Department") — UC 6 replaces the whole field in that case, as it always
    has — or when the desk is not a trailing segment, which would make the
    prefix an incomplete name rather than a whole one.
    """
    abbrev_spans = [m.span() for m in _ABBREV_SLASH_RE.finditer(text)]

    def _inside_abbrev(m: re.Match[str]) -> bool:
        return any(s < m.start() and m.end() <= e for s, e in abbrev_spans)

    segments: list[tuple[int, str]] = []
    pos = 0
    for m in _AP_DELIM_RE.finditer(text):
        if _inside_abbrev(m):
            continue
        segments.append((pos, text[pos:m.start()]))
        pos = m.end()
    segments.append((pos, text[pos:]))
    segments = [(start, seg) for start, seg in segments if seg.strip()]
    if len(segments) < 2:
        return None

    first_ap = next(
        (i for i, (_, seg) in enumerate(segments) if _segment_is_ap(seg)), None
    )
    # The desk must trail a real name: nothing to keep if it leads, and a
    # non-desk segment after it means the value is a longer chain this rule
    # has no reading of.
    if not first_ap:
        return None
    if not all(_segment_is_ap(seg) for _, seg in segments[first_ap:]):
        return None
    organisation = text[: segments[first_ap][0]].strip(" \t,;:/|-\u2013\u2014")
    return organisation or None


# ---------------------------------------------------------------------------
# UC 8 — Email copy (non-destructive)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
)


#: What a source system writes in FRONT of an address it is routing mail to:
#: "email invoices to:", "remit to", "AP e-mail". Once UC 8 has taken the
#: address into its own column this is all that is left of the field, and it
#: names nothing. Tested only on a field an address was just removed FROM, so
#: a slot that says "Invoices" and nothing else — a routing desk, like
#: Accounts Payable — is never read as a label.
_EMAIL_LABEL_WORDS = frozenset({
    "email", "e", "mail", "mailto", "send", "sent", "submit", "forward",
    "to", "for", "at", "via", "please", "all",
    "invoice", "invoices", "invoicing", "billing", "bill", "bills",
    "remit", "remittance", "remittances", "payment", "payments",
    "statement", "statements", "ap", "a", "p", "accounts", "payable",
    "address", "addresses", "contact",
})


def _is_email_label_only(text: str | None) -> bool:
    """True when *text* is nothing but the label that introduced an address."""
    if not text or not text.strip():
        return False
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", text)]
    return bool(words) and all(w in _EMAIL_LABEL_WORDS for w in words)


def _find_emails(text: str) -> list[str]:
    """Every email address in *text*, in order, deduplicated case-insensitively.

    All of them, not the first: one field routinely carries two ("Invoices
    ap@acme.com / ap2@acme.com"), and returning one meant the rest were
    stripped out of the field by UC 8's removal pass without ever reaching
    the Email column.

    The source string is not modified here; UC 8 removes what it captured.
    """
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for match in _EMAIL_RE.finditer(text):
        value = match.group(0)
        if value.lower() in seen:
            continue
        seen.add(value.lower())
        found.append(value)
    return found


# ---------------------------------------------------------------------------
# UC 9 — Address extraction
# ---------------------------------------------------------------------------

# Street-type suffixes as whole words.
_STREET_SUFFIXES = (
    r"St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|"
    r"Way|Hwy|Highway|Pl|Place|Pkwy|Parkway|Ct|Court|Ter|Terrace|"
    r"Cir|Circle|Sq|Square"
)
# Building / hall words that, when preceded by a HOUSE NUMBER, mark a
# campus-style street address (e.g. "100 Rhines Hall", "104 Weil Hall",
# "20 Smith Building"). Without a leading number a value like "Rhines Hall"
# is just a named building (routed to the Building field, not the street).
_BUILDING_PLACE_WORDS = (
    r"Hall|Building|Bldg|Pavilion|Tower|Annex|Wing|Complex"
)
# Patterns that indicate address content inside a name field.
# Street-name tokens allow both capitalised words ("Main", "Wolfe",
# "Torrey Pines") and numeric ordinals ("42nd", "5th", "1st") because
# real US addresses frequently use numeric street names.
_STREET_TOKEN = r"(?:[A-Z][\w\-]*|\d+(?:st|nd|rd|th))"
# Optional cardinal direction prefix / suffix — N, S, E, W,
# NW, NE, SW, SE, with optional dots (N.W., S.E.) or slashes.
_DIRECTION = r"(?:N\.?W\.?|N\.?E\.?|S\.?W\.?|S\.?E\.?|N\.?|S\.?|E\.?|W\.?)"
_ADDRESS_PATTERNS = [
    # "123 Main St", "600 N Wolfe St", "77 Massachusetts Ave",
    # "235 E 42nd St", "10 5th Ave", "1500 NW 12th Blvd",
    # "129000 N.W. 38th Avenue"
    re.compile(
        rf"\b\d+\s+(?:{_DIRECTION}\s+)?{_STREET_TOKEN}(?:\s+{_STREET_TOKEN})*\s+(?:{_STREET_SUFFIXES})(?:\s+{_DIRECTION})?\b\.?",
        re.IGNORECASE,
    ),
    # House number + named building/hall: "100 Rhines Hall", "20 Smith
    # Building". Requires the leading number so an unnumbered named building
    # ("Rhines Hall") is left for the Building-field router instead.
    re.compile(
        rf"\b\d+\s+(?:{_DIRECTION}\s+)?{_STREET_TOKEN}(?:\s+{_STREET_TOKEN})*\s+(?:{_BUILDING_PLACE_WORDS})\b\.?",
        re.IGNORECASE,
    ),
    # Named building/hall + room number: "Rhines Hall 104", "Smith Building
    # 200" (the number trails the building name). The trailing number keeps
    # an unnumbered building name out of this pattern.
    re.compile(
        rf"\b{_STREET_TOKEN}(?:\s+{_STREET_TOKEN})*\s+(?:{_BUILDING_PLACE_WORDS})\s+\d+[A-Za-z]?\b\.?",
        re.IGNORECASE,
    ),
    # Bare street names without house number:
    # "NW 2nd Ave", "S Main St", "E 42nd Street", "North Wolfe Road"
    re.compile(
        rf"^\s*(?:{_DIRECTION}\s+)?{_STREET_TOKEN}(?:\s+{_STREET_TOKEN})*\s+(?:{_STREET_SUFFIXES})(?:\s+{_DIRECTION})?\s*$",
        re.IGNORECASE,
    ),
    # "Suite 400", "Ste 400", "Unit 12", "Floor 3", "Bldg 4", "Room 12".
    # The marker needs a trailing word boundary and a space before its
    # value, otherwise "Unit" matches inside "United" and "Ste" inside
    # "Stevens", corrupting ordinary names.
    re.compile(r"\b(?:Suite|Ste|Unit|Floor|Bldg|Building|Room|Rm)\b\.?\s+[\w\-]+\b", re.IGNORECASE),
    # "PO Box 12345", "P.O. Box 12345", "Post Office Box 12345",
    # bare "Box 100", "Mail Box 5", "Mailbox 42"
    re.compile(
        r"\b(?:P\.?\s*O\.?\s*Box|Post\s+Office\s+Box|Mail\s*Box|Mailbox|Box)\s+\w+\b",
        re.IGNORECASE,
    ),
]


_STREET_TYPE_NORM = {
    "boulevard": "blvd", "avenue": "ave", "street": "st", "road": "rd",
    "drive": "dr", "lane": "ln", "parkway": "pkwy", "highway": "hwy",
    "court": "ct", "place": "pl", "terrace": "ter", "circle": "cir",
    "square": "sq", "suite": "ste",
}


def _norm_street_key(value: str | None) -> str:
    """Normalise a street value for duplicate comparison: lowercase, drop
    punctuation, canonicalise street-type words, collapse whitespace."""
    if not value:
        return ""
    s = re.sub(r"[.,]", " ", value.lower())
    toks = [_STREET_TYPE_NORM.get(t, t) for t in s.split()]
    return " ".join(toks).strip()


def _duplicates_existing_street(
    fragment: str, res: "PreprocessResult", house_number: str | None,
) -> bool:
    """True when *fragment* duplicates a populated street field — either the
    field verbatim, or the field prefixed with the record's House Number
    ("4200 Research Blvd" duplicates Street 1 "RESEARCH BLVD" + House Number
    "4200"). Normalised and case-insensitive."""
    nf = _norm_street_key(fragment)
    if not nf:
        return False
    hn = (house_number or "").strip()
    for slot in STREET_SLOTS:
        v = getattr(res, slot)
        if not (v and v.strip()):
            continue
        nv = _norm_street_key(v)
        if not nv:
            continue
        if nf == nv:
            return True
        if hn and nf == _norm_street_key(f"{hn} {v}"):
            return True
    return False


# ---------------------------------------------------------------------------
# A street line carrying a delivery instruction, written into a name slot
# ---------------------------------------------------------------------------
#
# "Crystalline Dr. Loading Dock" — a street line and the instruction telling
# the carrier where to unload, in one field, in the NAME block.
# `_ADDRESS_PATTERNS` cannot see it: the unnumbered-street pattern is anchored
# to the whole value and the qualifier trails it, and every other pattern wants
# a house number this record keeps in its own column. With nothing to route it,
# the slot reached the grounded lane, which classified it `noise` — the
# classification is right, and clearing the field on it threw the address away.
#
# `enrichment.address_processing._split_location_qualifier` splits the same
# shape off a STREET slot. It is not reused here because it requires a house
# number in the street part, which is exactly what a value sitting in the name
# block does not have; the vocabulary below is that function's, and the two are
# meant to stay in step.
_NAME_STREET_TYPES = (
    r"St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|"
    r"Hwy|Highway|Pkwy|Parkway|Ct|Court|Way|Pl|Place|Ter|Terrace|Cir|Circle"
)
_NAME_ACCESS_PREPOSITIONS = (
    r"Near|Behind|Beside|Adjacent|Across|Opposite|Next\s+To|In\s+Front\s+Of"
)
_NAME_ACCESS_LEAD = (
    rf"{_NAME_ACCESS_PREPOSITIONS}|"
    r"Gate|Dock|Loading|Unloading|Warehouse|Entrance|Entry|Bay|Ramp|Elevator|"
    r"Receiving|Shipping"
)
_NAME_ACCESS_PREPOSITION_RE = re.compile(
    rf"^(?:{_NAME_ACCESS_PREPOSITIONS})\b", re.IGNORECASE,
)
_NAME_STREET_ACCESS_RE = re.compile(
    rf"^(?P<street>\S+(?:\s+\S+)*?\s+(?:{_NAME_STREET_TYPES})\b\.?)\s+"
    rf"(?P<qual>(?:{_NAME_ACCESS_LEAD})\b.*)$",
    re.IGNORECASE,
)


def _split_street_access_qualifier(value: str | None) -> tuple[str, str] | None:
    """Split "<street line> <delivery instruction>" into its two halves.

    ``"Crystalline Dr. Loading Dock"`` → ``("Crystalline Dr", "Loading
    Dock")``. Returns None when *value* is not that shape.

    The street part needs at least one word in front of the street-type word,
    so a value that IS the instruction ("Dock Loading Bay") has no street to
    split off and is left for the address stage's logistics extractor. A part
    that reads as an organisation is refused outright — "Park Lane Shipping"
    is a company, not a street with an instruction after it.
    """
    if not value or not value.strip():
        return None
    match = _NAME_STREET_ACCESS_RE.match(value.strip())
    if not match:
        return None
    street = match.group("street").strip(" ,;-.")
    qualifier = match.group("qual").strip(" ,;-")
    if not street or not qualifier:
        return None
    if _street_is_org_name(street) or _street_is_org_name(value.strip()):
        return None
    # A one-word instruction is not enough to cut a name in two: "Park Lane
    # Shipping" is a company whose last word happens to be a logistics one,
    # and splitting it would file the company as a street. Either the
    # instruction names a place ("Loading Dock", "Receiving Bay") or it leads
    # with a positional preposition, which nothing else in a name does.
    if (
        len(qualifier.split()) < 2
        and not _NAME_ACCESS_PREPOSITION_RE.match(qualifier)
    ):
        return None
    return street, qualifier


def _record_site_conflict(
    res: PreprocessResult,
    field_name: str,
    place: str,
    city: str | None,
    region: str | None,
) -> None:
    """Note a site qualifier whose place the record's address contradicts.

    Silent when the two agree ("Merck Research Laboratories - Rahway, NJ" on a
    Rahway record says nothing new) and when the record states no place of its
    own to compare against — a contradiction needs two claims, and one is not
    manufactured out of a blank column.

    Only the first conflict is kept. A second one is the same finding about
    the same record, and a reason listing both asks a reviewer to settle two
    questions where there is one: which place this record is for.
    """
    if res.site_conflict is not None:
        return
    site_city, _, site_region = place.partition(",")
    verdict, _detail, _scope = compare_locality(
        stated_city=site_city.strip(),
        stated_region=site_region.strip(),
        city=city,
        region=region,
    )
    if verdict != CONTRADICTED:
        return
    label = NAME_SLOT_LABELS.get(field_name, field_name)
    stated = ", ".join(p for p in ((city or "").strip(), (region or "").strip()) if p)
    res.site_conflict = f"{label} states {place}; record says {stated}"


def _extract_addresses(text: str) -> tuple[list[str], str]:
    """Return (list of address fragments found, text with them removed)."""
    if not text:
        return [], text
    found: list[str] = []
    result = text
    for pat in _ADDRESS_PATTERNS:
        while True:
            m = pat.search(result)
            if not m:
                break
            found.append(m.group(0).strip(" ,;.:"))
            result = (result[: m.start()] + result[m.end():])
    result = re.sub(r"\s+", " ", result).strip(" ,;/|-")
    return found, result


# ---------------------------------------------------------------------------
# UC 11 — DBA ("Doing Business As") normalisation
# ---------------------------------------------------------------------------

# Match any variant of the "doing business as" marker and rewrite it to
# the canonical short form "DBA". Order matters: longest phrases first
# so that "Doing Business As" wins over a partial "D B A" match inside it.
_DBA_PATTERNS = [
    # "Doing Business As" — full phrase, any case, any whitespace.
    re.compile(r"\bdoing\s+business\s+as\b", re.IGNORECASE),
    # "D Business As" / "D. Business As" — typo'd / split form where
    # only the leading initial of "Doing" survives.
    re.compile(r"\bd\.?\s+business\s+as\b", re.IGNORECASE),
    # "D/B/A" with slashes, optional dots and inner whitespace.
    re.compile(r"\bd\s*\.?\s*/\s*b\s*\.?\s*/\s*a\b\.?", re.IGNORECASE),
    # "D.B.A." / "D. B. A." with dots.
    re.compile(r"\bd\.\s*b\.\s*a\.?", re.IGNORECASE),
    # "DBA" / "D B A" — letters with optional inner whitespace, last so
    # the dotted/slashed forms above match first.
    re.compile(r"\bd\s*b\s*a\b", re.IGNORECASE),
]


def _normalise_dba(text: str) -> tuple[str, bool]:
    """Replace any DBA variant in *text* with the canonical "DBA".

    Returns ``(normalised_text, changed)``. Surrounding text is preserved;
    only the DBA token itself is rewritten. Collapses any double spaces
    that result from the substitution.
    """
    if not text:
        return text, False
    result = text
    changed = False
    for pat in _DBA_PATTERNS:
        new_result, n = pat.subn("DBA", result)
        if n > 0:
            changed = True
            result = new_result
    if changed:
        result = re.sub(r"\s+", " ", result).strip(" ,;/|-")
    return result, changed


#: The canonical marker `_normalise_dba` leaves behind, at the head of a value
#: or after a separator. Stripped only to build a COMPARISON key — never from
#: the shipped value, which UC 11 restores intact because the marker is user
#: intent (legal name vs. trading name).
_DBA_MARKER_RE = re.compile(r"^\s*DBA\b[\s:,\-/]*", re.IGNORECASE)


def dba_payload(text: str | None) -> str | None:
    """The trading name a DBA-marked value carries, or None.

    "DBA Olin E Teague Vet CTR" -> "Olin E Teague Vet Center". The marker
    comes off and abbreviations are expanded, because every consumer of this
    is comparing against a canonical form: `subject_preserved` and
    `classify_name_change` both read "DBA" as a token the candidate failed to
    account for, and "CTR" as a word the candidate replaced. Neither is a
    statement about which entity the record names.

    Returns None when the value carries no marker or no payload behind it.
    """
    if not text or not str(text).strip():
        return None
    normalised, _changed = _normalise_dba(str(text))
    stripped = _DBA_MARKER_RE.sub("", normalised).strip(" ,;:/|-")
    if not stripped or stripped == normalised.strip():
        return None
    return (expand_abbreviations(stripped) or stripped).strip() or None


# ---------------------------------------------------------------------------
# UC 10 — Opaque code / meaningless identifier detection
# ---------------------------------------------------------------------------

# Matches values that are clearly internal codes, not names:
# - Pure digits (5+): "800000070"
# - Letter prefix + digits (5+): "B800000070", "X12345"
# - Letter(s) + digits with optional dashes: "SAP-123456", "BP-00042"
_OPAQUE_CODE_RE = re.compile(
    r"^\s*[A-Za-z]{0,4}[-]?\d{5,}\s*$",
)


#: A label a source system writes in front of a reference number — "Ref#
#: E004120188", "No. 4471120". The label is not part of a name and does not
#: turn the code into one, so it is removed before the code test.
_CODE_LABEL_RE = re.compile(
    r"^\s*(?:ref(?:erence)?|no|nbr|num(?:ber)?|acct|account|id|code|po)\s*"
    r"[#:.\-]*\s*",
    re.IGNORECASE,
)


def _is_opaque_code(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if _OPAQUE_CODE_RE.match(stripped):
        return True
    # "Ref# E004120188" is the same value as "E004120188" with a label on the
    # front. Without this the label alone was enough to make the code read as
    # a name, and the record shipped asking a steward to establish the
    # canonical form of a reference number.
    labelled = _CODE_LABEL_RE.sub("", stripped)
    return bool(labelled != stripped and _OPAQUE_CODE_RE.match(labelled))


# A leading account/customer code: 1-4 letters then ≥2 digits ("B800000123",
# "NT30", "SAP-42"). A LETTER PREFIX is required so a leading HOUSE NUMBER
# (pure digits, e.g. "10901 Roosevelt Blvd N") is never mistaken for a code.
_LEADING_ACCOUNT_CODE_RE = re.compile(r"^[A-Za-z]{1,4}-?\d{2,}$")


def _collapse_repeated_phrase(value: str | None) -> str | None:
    """Collapse a value that is the same phrase repeated back-to-back.

    "Department of Central Receiving Department of Central Receiving"
        → "Department of Central Receiving"
    "Receiving Receiving" → "Receiving"

    Returns the value unchanged when it is not a whole-number repetition of a
    shorter token sequence (case-insensitive comparison, original casing of
    the first occurrence is kept).
    """
    if not value or not value.strip():
        return value
    tokens = value.split()
    n = len(tokens)
    if n < 2:
        return value
    low = [t.lower() for t in tokens]
    for p in range(1, n // 2 + 1):
        if n % p != 0:
            continue
        unit = low[:p]
        if all(low[i * p:(i + 1) * p] == unit for i in range(n // p)):
            return " ".join(tokens[:p])
    return value


# Words skipped when computing an initialism, so "University of California, Los
# Angeles" → UCLA (not UOCLA).
_ACRONYM_STOPWORDS = {
    "of", "and", "the", "for", "at", "in", "on", "de", "la", "le", "du",
    "des", "von", "van", "der", "el", "&", "a",
}


def _is_acronym_token(token: str) -> bool:
    """True when *token* looks like an all-caps acronym (2-8 letters, allowing
    interior dots / ampersands): "MIT", "U.C.L.A.", "AT&T"."""
    letters = re.sub(r"[^A-Za-z]", "", token)
    if not (2 <= len(letters) <= 8):
        return False
    # Every letter present must be upper-case (so "MIT" qualifies, "Mit" does not).
    return letters.isupper()


def _acronym_matches_phrase(acronym: str, phrase: str) -> bool:
    """True when *acronym* is the initialism of *phrase* (a multi-word full
    form). Tries initials of the significant words (skipping stopwords) and of
    all words, so both "UCLA" (University California Los Angeles) and "IBM"
    (International Business Machines) match.
    """
    acro = re.sub(r"[^A-Za-z]", "", acronym).upper()
    if len(acro) < 2:
        return False
    words = re.findall(r"[A-Za-z]+", phrase)
    if len(words) < 2:
        return False
    initials_all = "".join(w[0] for w in words).upper()
    sig = [w for w in words if w.lower() not in _ACRONYM_STOPWORDS]
    initials_sig = "".join(w[0] for w in sig).upper()
    return acro in (initials_all, initials_sig)


# Dash-delimited acronym+full form: "ACRO - Full" or "Full - ACRO". The full
# side must have >=3 words so proper-noun hyphenations ("Heriot-Watt University",
# "Bio-Rad", "Dana-Farber", "Hahn-Schickard-Gesellschaft") are never split.
_DASH_ACRONYM_RE = re.compile(r"^\s*(.+?)\s*[-‐-―]\s*(.+?)\s*$")
# A dash with whitespace on at least one side (" - ", "- ", " -"). This marks an
# abbreviation-expansion pair ("Tuhh - Hamburg University of Technology"); an
# UNSPACED hyphen ("Dana-Farber") is a compound proper noun and never split
# unless the acronym is a verified initialism ("…TECHNOLOGY-NIST").
_SPACED_DASH_RE = re.compile(r"\s[-‐-―]|[-‐-―]\s")


def _dash_acronym_full(value: str) -> tuple[str, str, bool] | None:
    """For a dash-delimited value where one side is a single short token and the
    other is a >=3-word phrase, return (acronym, full, is_verified_initialism)
    or None."""
    m = _DASH_ACRONYM_RE.match(value)
    if not m:
        return None
    a, b = m.group(1).strip(), m.group(2).strip()

    def _short_tok(t: str) -> bool:
        letters = re.sub(r"[^A-Za-z]", "", t)
        return " " not in t and 2 <= len(letters) <= 8

    def _nwords(t: str) -> int:
        return len(re.findall(r"[A-Za-z]+", t))

    if _short_tok(a) and _nwords(b) >= 3:
        acro, full = a, b
    elif _short_tok(b) and _nwords(a) >= 3:
        acro, full = b, a
    else:
        return None
    return acro, full, _acronym_matches_phrase(acro, full)


def _syllabic_dash_abbrev(value: str | None) -> str | None:
    """Flag reason when a dash-delimited value pairs a full org name with a
    SYLLABIC abbreviation that is not a verified initialism ("CALIBR -
    California Institute for Biomedical Research", "Tuhh - Hamburg University of
    Technology") — so it is not silently kept as-is. Else None."""
    if not value or not value.strip():
        return None
    if _parent_org_split(value):
        return None  # a parent + unit, not an abbreviation of anything
    dash = _dash_acronym_full(re.sub(r"\s+", " ", value.strip()))
    if dash and not dash[2]:
        return (
            f"dash abbreviation {dash[0]!r} is not a verified initialism of "
            f"{dash[1]!r} — review"
        )
    return None


# A leading single token followed by a dash: "USDA - Kerrville MLRA Office".
# Deliberately narrower than `_DASH_ACRONYM_RE` — the parent must be the FIRST
# side. "Kerrville MLRA Office - USDA" is not how master data writes ownership,
# and accepting it would let a trailing acronym pull an unrelated unit into the
# organisation slot.
_PARENT_DASH_RE = re.compile(r"^\s*([A-Za-z][A-Za-z.&]*)\s*[-‐-―]\s*(.+?)\s*$")


def _parent_org_acronym(token: str) -> str | None:
    """The official full name for *token* when it is a known parent-org
    acronym, else None. Case- and punctuation-insensitive ("U.S.D.A.")."""
    letters = re.sub(r"[^A-Za-z]", "", token).upper()
    return PARENT_ORG_ACRONYMS.get(letters) if letters else None


def _parent_org_split(value: str | None) -> tuple[str, str] | None:
    """Split "<parent acronym> <unit>" into (expanded parent, unit), else None.

    "USDA - Kerrville MLRA Office"   → ("United States Department of Agriculture",
                                        "Kerrville MLRA Office")
    "NASA Ames Research Center"      → ("National Aeronautics and Space
                                        Administration", "Ames Research Center")

    Name 1 in these values holds two different things: the organisation that
    owns the record and the unit inside it. `utils.name_slots` gives each its
    own slot — Name 1 the organisation, Names 2..N the units — so the split is
    what the block was already shaped for.

    Returns None when the two sides are the SAME entity written twice ("FDA -
    Food and Drug Administration"). That is a redundant restatement, not
    ownership, and :func:`_strip_redundant_acronym` resolves it to the full
    form. The check is deliberately both ways round — against the acronym's
    own expansion and against the initials of the tail — so neither "USDA -
    United States Department of Agriculture" nor "FDA - Food & Drug
    Administration" is mistaken for a parent and its child.
    """
    if not value or not value.strip():
        return None
    v = re.sub(r"\s+", " ", value.strip())

    m = _PARENT_DASH_RE.match(v)
    if m:
        head, tail = m.group(1), m.group(2)
    else:
        parts = v.split()
        if len(parts) < 2:
            return None  # a bare acronym is the organisation, with no unit
        head, tail = parts[0], " ".join(parts[1:])

    full = _parent_org_acronym(head)
    if not full:
        return None
    tail = tail.strip(" ,;-")
    if not tail:
        return None
    if tail.lower() == full.lower() or _acronym_matches_phrase(head, tail):
        return None
    return full, tail


def _insert_dept_value(res: "PreprocessResult", value: str) -> list[str]:
    """Insert *value* at the head of the department block, shifting the values
    already there down one slot. Returns whatever was pushed off the end.

    Existing values are re-laid-out packed, so a block with a gap in it closes
    the gap on the way through rather than spending a slot on it. UC 14 packs
    the block again later; doing it here too is what keeps a gap from being
    the reason a real value overflows.
    """
    current = [
        v.strip() for slot in DEPT_SLOTS
        if (v := getattr(res, slot)) and v.strip()
    ]
    packed = [value, *current]
    for slot, val in zip(DEPT_SLOTS, [*packed, *([None] * len(DEPT_SLOTS))]):
        setattr(res, slot, val)
    return packed[len(DEPT_SLOTS):]


def _strip_redundant_acronym(value: str | None) -> str | None:
    """When a name carries BOTH an acronym and its full form for the same
    entity, keep only the full form.

    "MIT Massachusetts Institute of Technology" → "Massachusetts Institute of Technology"
    "Massachusetts Institute of Technology (MIT)" → "Massachusetts Institute of Technology"
    "MIT (Massachusetts Institute of Technology)" → "Massachusetts Institute of Technology"
    "FDA - Food & Drug Administration" → "Food and Drug Administration"

    Only fires when the acronym is the verified initialism of the full form, so
    unrelated leading/trailing tokens ("UC Berkeley", "3M Company") and
    hyphenated proper nouns ("Heriot-Watt University", "Bio-Rad") are untouched.
    """
    if not value or not value.strip():
        return value
    v = re.sub(r"\s+", " ", value.strip())

    # A known parent organisation and one of its units is not a name written
    # twice — nothing here is redundant, and the acronym must survive. Handled
    # by the parent split in `preprocess_record`; this guard also covers the
    # second caller (the pipe splitter), which has no such step.
    if _parent_org_split(v):
        return value

    # Dash form: "ACRO - Full" / "Full - ACRO". Always resolve to the full form
    # (an abbreviation and its full form must never coexist in Name 1). The
    # caller flags the value when the abbreviation was syllabic, not a verified
    # initialism. "&" → "and", case-matched so an ALL-CAPS full form is still
    # title-cased downstream.
    dash = _dash_acronym_full(v)
    if dash and (dash[2] or _SPACED_DASH_RE.search(v)):
        # Strip when the acronym is a verified initialism (spaced or not), OR
        # when it is a spaced abbreviation-expansion pair ("Tuhh - Hamburg …").
        # An unspaced, unverified hyphen ("Dana-Farber Cancer Institute") is a
        # proper noun and left intact.
        full = dash[1]
        repl = " AND " if full.isupper() else " and "
        full = re.sub(r"\s*&\s*", repl, full).strip(" ,;-")
        return full or value

    # Parenthetical form: "Full (ACRO)" or "ACRO (Full)".
    m = re.search(r"^(.*?)\s*\(([^)]+)\)\s*(.*)$", v)
    if m:
        outer = (m.group(1) + " " + m.group(3)).strip(" ,;-")
        inner = m.group(2).strip(" ,;-")
        if _is_acronym_token(inner) and _acronym_matches_phrase(inner, outer):
            return outer or v
        if (
            outer and _is_acronym_token(outer)
            and _acronym_matches_phrase(outer, inner)
        ):
            return inner or v

    # Adjacent form: leading or trailing acronym next to the full phrase.
    tokens = v.split()
    if len(tokens) >= 3:
        if _is_acronym_token(tokens[0]) and _acronym_matches_phrase(
            tokens[0], " ".join(tokens[1:])
        ):
            return " ".join(tokens[1:]).strip(" ,;-")
        if _is_acronym_token(tokens[-1]) and _acronym_matches_phrase(
            tokens[-1], " ".join(tokens[:-1])
        ):
            return " ".join(tokens[:-1]).strip(" ,;-")

    return value


def _strip_leading_opaque_code(value: str | None) -> str | None:
    """Strip a leading account/opaque-code token from a name value when
    real content follows it.

    "B800000123 c/o Dr. Mark Adams" → "c/o Dr. Mark Adams"
    "NT30 Division of Cardiology"   → "Division of Cardiology"

    Leaves a value that is ONLY a code untouched (handled by UC 10), never
    strips a leading house number ("10901 Roosevelt Blvd N"), and never
    touches a leading number that is part of a name ("3M", "21st Century
    Fox", "100 Black Men of America") — only a letter-prefixed code matches.
    """
    if not value or not value.strip():
        return value
    out = value.strip()
    while True:
        parts = out.split(None, 1)
        if len(parts) == 2 and _LEADING_ACCOUNT_CODE_RE.match(parts[0].strip(",;:|/")):
            out = parts[1].strip()
        else:
            break
    return out


# Residual junk that lingers in the Name 3 overflow slot after the
# structured extractors (email / address / contact) have run: web URLs,
# phone / fax numbers, and standalone opaque codes. Stripped so the
# tertiary name value is not polluted.
_URL_RE = re.compile(
    r"\b(?:https?://|www\.)\S+"
    # Bare domain ("acme.com"); the (?<!@) guard avoids matching the
    # domain part of an email address (e.g. the "x.com" in "foo@x.com").
    r"|(?<!@)\b[A-Za-z0-9][A-Za-z0-9\-]*\."
    r"(?:com|org|net|edu|gov|io|co|us|biz|info|gmbh|de|uk|ca)\b(?:/\S*)?",
    re.IGNORECASE,
)
# Phone / fax: optional label, then 3+ digit groups separated by space,
# dot or dash (so a lone number or short code is not mistaken for one).
_PHONE_RE = re.compile(
    r"(?:\b(?:tel|telephone|phone|fax|ph|cell|mobile|mob)\b\.?[:\s#]*)?"
    r"\+?\(?\d{2,4}\)?(?:[\s.\-]\d{2,4}){2,4}"
    r"(?:\s*(?:ext|x|extension)\.?\s*\d+)?",
    re.IGNORECASE,
)
# A standalone token that is purely a code: 4+ bare digits, or a short
# letter prefix followed by 2+ digits ("NT30", "X12345", "SAP-42").
_OPAQUE_CODE_TOKEN_RE = re.compile(r"^(?:\d{4,}|[A-Za-z]{1,4}-?\d{2,})$")


def _strip_dept_slot_junk(value: str) -> str | None:
    """Strip URLs, phone/fax numbers and standalone opaque codes from a
    department-slot value, returning the tidied remainder (or None).

    Applies to every slot below Name 1. Name 3 was the original overflow
    slot this cleaned, but the same stray URL / phone / code residue lands
    in Name 2 and the slots below Name 3 just as readily. Name 1 is
    excluded: an opaque code there is the organisation name the record
    supplied, and UC 10 flags it for a reviewer rather than deleting it.
    """
    out = _URL_RE.sub(" ", value)
    out = _PHONE_RE.sub(" ", out)
    kept = [
        tok for tok in out.split()
        if not _OPAQUE_CODE_TOKEN_RE.match(tok.strip(",;:|/"))
    ]
    out = re.sub(r"\s+", " ", " ".join(kept)).strip(" ,;/|-")
    return out or None


def _strip_street_junk(value: str) -> str | None:
    """Strip URLs and phone/fax numbers from a street value.

    Unlike the name slots, opaque numeric codes are NOT stripped — a street number
    is itself numeric, so removing bare-digit tokens would destroy the
    address.
    """
    out = _URL_RE.sub(" ", value)
    out = _PHONE_RE.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip(" ,;/|-")
    return out or None


# A person sitting in a street slot: a title prefix, 1-4 capitalised name
# words, optionally followed by a "- role" / ", role" suffix. Group 1 is
# the clean person name. The pattern's tight name shape plus the street-
# suffix guard below avoid matching person-named streets ("Dr Martin
# Luther King Jr Blvd", "Dr Sarah Johnson Ave").
_STREET_PERSON_RE = re.compile(
    r"^\s*("
    r"(?:Dr|Prof|Professor|Mr|Mrs|Ms|Mx|Sir|Rev|Hon)\.?\s+"
    r"[A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+){0,3}"
    r")(?:\s*[-,]\s*.+)?$",
    re.IGNORECASE,
)
# A street suffix that marks the value as an actual road appears at the
# END of the line (optionally with a trailing direction), e.g.
# "...King Jr Blvd", "...Johnson Ave". Anchoring to the end avoids
# matching the leading title "Dr" (which doubles as the "Drive" suffix).
_STREET_SUFFIX_GUARD_RE = re.compile(
    rf"\b(?:{_STREET_SUFFIXES})\b\.?"
    r"(?:\s+(?:N|S|E|W|NE|NW|SE|SW|North|South|East|West))?\s*$",
    re.IGNORECASE,
)


def _street_person_name(value: str) -> str | None:
    """Return the person name if *value* is entirely a title-prefixed
    person reference and is NOT street-like, else None."""
    if _STREET_SUFFIX_GUARD_RE.search(value):
        return None
    m = _STREET_PERSON_RE.match(value.strip())
    return m.group(1).strip() if m else None


# UC 16 — institution + embedded department split.
# A trailing "Department of X" / "Division of X" phrase in Name 1.
_DEPT_PHRASE_RE = re.compile(
    r"\s+((?:Department|Dept\.?|Division|Div\.?)\s+(?:of|for)\s+.+)$",
    re.IGNORECASE,
)
# The Name 1 prefix qualifies as an institution (so the trailing department
# is a sub-unit to move out) only if it carries an institution keyword.
# Jurisdictions like "United States" / "Florida" / "Wisconsin" do NOT, so
# government agencies such as "United States Department of Agriculture" or
# "Florida Department of Health" are left intact in Name 1.
_INSTITUTION_PREFIX_RE = re.compile(
    r"\b(?:University|College|Institute|Polytechnic|Hospital|Clinic|"
    r"School|Center|Centre|Foundation|"
    r"Laborator(?:y|ies)|Labs?|"
    r"Inc|Incorporated|Corp|Corporation|Company|LLC|Ltd)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# UC 15 — c/o and ATTN extraction from Name 2 (5-case classification)
# ---------------------------------------------------------------------------
#
# When Name 2 carries a "c/o" or "Attn:" prefix (or is itself an email,
# or a clear job title), the payload is routed to one of four output
# fields based on what it looks like. Priority order:
#
#   D — Email          → e_mail, clear Name 2
#   C — Department     → keep in Name 2 (prefix stripped)
#   B — Company        → care_of, clear Name 2
#   A — Person         → care_of (with title) + contact (without) +
#                        clear Name 2 + flag for SERP dept lookup
#   E — Fallback       → care_of, clear Name 2
#
# Cases A/B/C/E only fire when a prefix is present (otherwise the raw
# Name 2 value is the user's department/sub-org and must not be moved).
# Case D fires even without a prefix when the entire Name 2 value is
# an email address. Case E also fires without a prefix when the value
# is unambiguously a job title (ends in Manager / Director / etc.).

#: The c/o | Attn marker itself, bounded on BOTH sides. One definition, used
#: by every matcher that looks for it — the address stage imports it.
#:
#: The boundaries are the whole point. `att?n+` is a substring of ordinary
#: words, and applied unbounded it matched the "ATN" inside "BOATNER": record
#: 13337073's "307 BOATNER RD, STE 1" was cut at the sixth character and
#: shipped `Street 1 = "307 Bo"` with `Care Of = "Er Rd, Ste 1"`, the suite
#: swallowed into a c/o payload that no marker ever introduced. "9 PATTON DR"
#: and "40 CATTNER Blvd" fail the same way.
#:
#: `_ATTN_RE` (UC 7, below) already carried `\b` on both sides and is the model
#: this follows. Both boundaries are needed: a leading one alone still admits
#: "ATTNER Blvd", where the marker opens the word but does not end it.
#:
#: ``att?n+`` also catches the common misspelling "Atnn:" (and "attnn").
_CO_ATTN_MARKER = r"\b(?:c\s*/\s*o|att?n+(?:ention|tion)?|att)\b"

# Anchored to the start so "c/o" inside a phrase doesn't accidentally match.
_CO_ATTN_PREFIX_RE = re.compile(
    r"^\s*" + _CO_ATTN_MARKER + r"\s*[:\-]?\s*",
    re.IGNORECASE,
)

# Parenthetical noise (e.g. "(guest)", "(visitor)") stripped from the
# payload after prefix removal but before classification.
_PARENTHETICAL_NOISE_RE = re.compile(r"\s*\([^)]*\)\s*")

# Department / functional-unit signals. Match anywhere in the payload.
_DEPT_KEYWORDS_RE = re.compile(
    r"\b("
    r"Department\s+of|Dept\.?\s+of|Division\s+of|"
    r"School\s+of|College\s+of|Faculty\s+of|"
    r"Accounts?\s+Payable|Acct?s?\.?\s+Payable|"
    r"Purchasing|Procurement|"
    r"Receiving|Shipping|Billing"
    r")\b",
    re.IGNORECASE,
)

# A named building sitting in a name field — a value that ENDS in
# "Building"/"Bldg" with at least one descriptive word before it
# (e.g. "Neil Armstrong Operations and Checkout Building"). This is a
# physical location, not a department, so it is routed to the Building
# address output. A bare "Building" / "Bldg" (no descriptor) is ignored,
# as is the "Building <id>" identifier form which the address extractor
# already handles on street fields.
_NAMED_BUILDING_RE = re.compile(r"\S.*\s(?:Building|Bldg)\.?\s*$", re.IGNORECASE)
_BUILDING_IDENTIFIER_RE = re.compile(r"^(?:Building|Bldg)\.?\s+\w[\w\-]*\s*$", re.IGNORECASE)


# A value made up ENTIRELY of location-descriptor + identifier groups —
# "Wing C", "Annex D Pod 2", "Bay 4", "Floor 3 Room 12". These are address
# sub-locations, not organisational names, and are moved verbatim (as one
# unit) to an empty street slot. Each identifier must look like an identifier
# (contains a digit, or is 1-2 chars) so "Wing Commander" is NOT matched.
_LOC_DESCRIPTORS = (
    r"Wing|Annex|Pod|Bay|Block|Module|Dock|Gate|Level|Hall|Pavilion|Tower|"
    r"Suite|Ste|Unit|Floor|Fl|Room|Rm|Bldg|Building|Mail\s*Stop|MS"
)
_LOC_ID = r"(?:[\w]*\d[\w]*|[A-Za-z]{1,2})"
# Descriptor and identifier may be separated by whitespace OR a hyphen
# ("Wing C", "Wing-C", "Pod-2").
_LOCATION_FRAGMENT_RE = re.compile(
    rf"^(?:(?:{_LOC_DESCRIPTORS})\.?[\s\-]+{_LOC_ID}\b[\s,]*)+$", re.IGNORECASE,
)


def _location_fragment(text: str | None) -> str | None:
    """Return the trimmed value when *text* is purely location sub-locations
    (e.g. "Wing C", "Annex D Pod 2"), else None."""
    if not text or not text.strip():
        return None
    stripped = re.sub(r"\s+", " ", text.strip())
    return stripped if _LOCATION_FRAGMENT_RE.match(stripped) else None


# A campus / site label sitting in a name field — "Sarasota Campus", "West
# Campus", "Golden Colorado Campus", "Oxford Science Park". A campus says
# WHERE the organisation sits, not which part of it this record is, so it is
# an address line and belongs in a street slot, never in the Name block.
#
# The designator must be the TRAILING word, with one to three qualifying words
# in front of it. That is what separates a site label from a department that
# merely happens to sit on a campus ("Campus Health Services", "Campus Store",
# "North Campus Research Complex"), which stays a Name value.
_SITE_DESIGNATOR = (
    r"Campus|Science\s+Park|Research\s+Park|Technology\s+Park|Business\s+Park"
)
_SITE_FRAGMENT_RE = re.compile(
    rf"^(?:[\w&'’\-\.]+\s+){{1,3}}(?:{_SITE_DESIGNATOR})$", re.IGNORECASE,
)


def _site_fragment(text: str | None) -> str | None:
    """Return the trimmed value when *text* is a bare campus / science-park
    site label ("Sarasota Campus"), else None.

    A value carrying a house number or a street type ("1700 Campus Dr",
    "Campus Box 7212") is a street line the address extractor already routes,
    and a value with a department signal is a unit of the organisation — both
    return None.
    """
    if not text or not text.strip():
        return None
    stripped = re.sub(r"\s+", " ", text.strip())
    if "," in stripped or "|" in stripped:
        return None
    if re.match(r"^\d", stripped):
        return None
    if _DEPT_KEYWORDS_RE.search(stripped) or _DEPT_LEAD_RE.match(stripped):
        return None
    if _segment_is_address(stripped) or _named_building(stripped):
        return None
    return stripped if _SITE_FRAGMENT_RE.match(stripped) else None


def _named_building(text: str | None) -> str | None:
    """Return the trimmed value when *text* is a named building, else None."""
    if not text or not text.strip():
        return None
    stripped = text.strip()
    # A multi-segment value ("… Laboratory, Lab 576, Chemistry Bldg") is not a
    # single named building — let the comma/pipe splitters route each segment.
    if "," in stripped or "|" in stripped:
        return None
    # A leading house number makes this a street address ("100 Rhines Hall",
    # "20 Smith Building"), not a bare named building — leave it for the
    # address extractor to route into a street slot.
    if re.match(r"^\d+\s", stripped):
        return None
    if _BUILDING_IDENTIFIER_RE.match(stripped):
        return None
    if _DEPT_KEYWORDS_RE.search(stripped):
        return None
    if _NAMED_BUILDING_RE.match(stripped):
        return stripped
    return None

# "Services" as the trailing word (e.g. "Fabrication Outage Services").
_TRAILING_SERVICES_RE = re.compile(r"\bServices\s*$", re.IGNORECASE)

# Legal-entity suffixes that mark a payload as a company.
_LEGAL_SUFFIX_RE = re.compile(
    r"\b("
    r"L\.?L\.?C\.?|"
    r"Inc\.?|Incorporated|"
    r"Corp\.?|Corporation|"
    r"Ltd\.?|Limited|"
    r"Co\.?|Company|"
    r"L\.?L\.?P\.?|L\.?P\.?|"
    r"GmbH|S\.?A\.?(?:S\.?)?|"
    r"AG|N\.?V\.?|B\.?V\.?|"
    r"PLC|Pty|"
    r"P\.?C\.?"
    r")\.?\b",
    re.IGNORECASE,
)

# Job titles that, even without a c/o prefix, indicate the value is
# metadata about a person rather than a sub-organisation.
_JOB_TITLE_TAIL_RE = re.compile(
    r"\b("
    r"Manager|Director|Supervisor|Coordinator|Specialist|"
    r"Officer|Lead|Head|Chair|Administrator|Analyst|"
    r"Representative|Executive|Foreman|Assistant|"
    r"Buyer|Planner|Controller"
    r")\s*$",
    re.IGNORECASE,
)

# Title prefix used for the contact-vs-care_of split (Case A).
_TITLE_STRIP_RE = re.compile(
    r"^\s*(?:Dr|Prof|Professor|Mr|Mrs|Ms|Mx|Sir|Ir|Engr|Rev|Hon)\.?\s+",
    re.IGNORECASE,
)


def _strip_co_attn_prefix(text: str) -> tuple[str, bool]:
    """Return (payload, had_prefix). Prefix is stripped at most once."""
    if not text:
        return text, False
    m = _CO_ATTN_PREFIX_RE.match(text)
    if not m:
        return text.strip(), False
    return text[m.end():].strip(), True


def _strip_parenthetical_noise(text: str) -> str:
    # Strips parenthetical noise like "(guest)" and tidies surrounding
    # whitespace. Does NOT strip trailing periods — they're load-bearing
    # for legal suffixes like "Inc." that Case B relies on.
    cleaned = _PARENTHETICAL_NOISE_RE.sub(" ", text)
    return re.sub(r"\s+", " ", cleaned).strip(" ,;:-")


# A name field that OPENS with a bracketed span, then carries more text:
# "(Julius-Maximilians Universität Würzburg) Gieshügeler Str.46".
_LEADING_BRACKET_RE = re.compile(r"^\s*[(\[{]([^(){}\[\]]*)[)\]}]\s*(.+)$", re.DOTALL)


def _leading_bracketed_name(value: str | None) -> tuple[str, str] | None:
    """Split "(Org) rest" into ``(org, rest)`` when the org is in the brackets.

    UC 12 drops a bracketed span because a TRAILING one is a disambiguator
    ("3M (Detroit)"). A LEADING one is the opposite shape: the typist opened
    with the organisation and ran the address on after it, so dropping the
    span would keep the street and throw the name away.

    Returns None unless the value opens with a closed bracket, has text after
    it, and the bracketed content reads as name-block content — so
    "(guest) John Smith" and "(US) Widgets Ltd" fall through to the plain
    strip. Position alone is not enough; what is inside has to be a name.
    """
    if not value:
        return None
    m = _LEADING_BRACKET_RE.match(value)
    if not m:
        return None
    inner = re.sub(r"\s+", " ", m.group(1)).strip(" ,;:-/|")
    rest = re.sub(r"\s+", " ", m.group(2)).strip(" ,;:-/|")
    if not inner or not rest:
        return None
    if not _looks_like_name_content(inner):
        return None
    return inner, rest


def _is_department_payload(text: str) -> bool:
    if not text:
        return False
    if _DEPT_KEYWORDS_RE.search(text):
        return True
    if _is_ap_reference(text):
        return True
    if _TRAILING_SERVICES_RE.search(text):
        return True
    return False


def _has_legal_suffix(text: str) -> bool:
    return bool(text and _LEGAL_SUFFIX_RE.search(text))


# Long-form legal-entity suffixes → canonical abbreviation. These long
# forms are unambiguous legal designators (a name never contains
# "Aktiengesellschaft" or "Incorporated" as a distinctive word), so they
# are collapsed to the short form during preprocessing. This keeps a name
# like "Carl Zeiss Aktiengesellschaft" on the SAME enrichment path as its
# sibling "Carl Zeiss AG": both then feed the identical string into ROR,
# the company-canonical LLM, the SERP query, and search-term derivation.
# Without it the long form misses ROR, passes the raw suffix into the web
# search, and resolves to an unrelated site.
_LEGAL_LONGFORM_SUBS = [
    (re.compile(r"\bGesellschaft\s+mit\s+beschr[äa]nkter\s+Haftung\b", re.IGNORECASE), "GmbH"),
    (re.compile(r"\bLimited\s+Liability\s+Company\b", re.IGNORECASE), "LLC"),
    (re.compile(r"\bLimited\s+Liability\s+Partnership\b", re.IGNORECASE), "LLP"),
    (re.compile(r"\bAktiengesellschaft\b", re.IGNORECASE), "AG"),
    (re.compile(r"\bIncorporated\b", re.IGNORECASE), "Inc"),
    (re.compile(r"\bCorporation\b", re.IGNORECASE), "Corp"),
]


def _normalise_legal_suffix(text: str | None) -> tuple[str, bool]:
    """Collapse long-form legal suffixes to their canonical abbreviation.

    Returns ``(normalised_text, changed)``. Bare ambiguous words
    ("Limited", "Company") are intentionally left alone — they appear as
    real name components ("Limited Brands", "The Walt Disney Company").
    """
    if not text:
        return text or "", False
    out = text
    for pat, repl in _LEGAL_LONGFORM_SUBS:
        out = pat.sub(repl, out)
    out = re.sub(r"\s+", " ", out).strip()
    return out, out != text


def _is_job_title(text: str) -> bool:
    return bool(text and _JOB_TITLE_TAIL_RE.search(text))


def _strip_title(name: str) -> str:
    return _TITLE_STRIP_RE.sub("", name).strip()


def _extract_co_attn_from_slot(
    res: PreprocessResult,
    slot: str,
    llm_person_verdicts: dict[str, str],
) -> bool:
    """Apply the 5-case c/o + ATTN classifier to one department slot.

    Mutates *res* in place. Returns True if the slot was rewritten or
    cleared so the caller can skip the legacy UC 7 Pattern A loop for
    that field.

    Runs for every slot below Name 1: a "c/o" or "ATTN:" prefix is just as
    likely to land in Name 3 or Name 4 as in Name 2 — the SAP entry order
    decides which, and the routing decision is identical either way. Name 1
    is excluded because it holds the organisation, not routing text.
    """
    val = getattr(res, slot)
    if not val or not val.strip():
        return False

    payload, had_prefix = _strip_co_attn_prefix(val)
    payload = _strip_parenthetical_noise(payload)
    if not payload:
        # Prefix-only value ("Attn:") — nothing to route, just clear it.
        if had_prefix:
            setattr(res, slot, None)
            res.note(15, f"{slot} cleared — prefix only, no payload")
            return True
        return False

    # ── Case D: Email ────────────────────────────────────────────────
    email_match = _EMAIL_RE.search(payload)
    if email_match:
        email = email_match.group(0)
        payload_is_just_email = re.sub(r"\s+", " ", payload).strip() == email
        if had_prefix or payload_is_just_email:
            outcome = res.add_email(email)
            if outcome == "conflict":
                # Both are kept. This branch used to record neither — it
                # flagged the conflict and then cleared the slot regardless,
                # so the address it had just refused to write went nowhere.
                res.note(
                    15,
                    f"Case D: second email '{email}' in {slot} — kept "
                    f"alongside the address already captured, flag for review",
                )
                if "email-conflict" not in res.flags:
                    res.flags.append("email-conflict")
            else:
                res.note(15, f"Case D: email extracted from {slot} ({email})")
            setattr(res, slot, None)
            return True

    # ── No prefix: only Case E (clear job title) fires ───────────────
    if not had_prefix:
        if _is_job_title(payload):
            _set_care_of(res, slot, payload)
            setattr(res, slot, None)
            res.note(15, f"Case E: job title routed to care_of ({payload!r})")
            return True
        return False

    # ── Case C: Department / Functional Unit ─────────────────────────
    if _is_department_payload(payload):
        setattr(res, slot, payload)
        res.note(15, f"Case C: department/unit kept in {slot} (prefix stripped, was {val!r})")
        return True

    # ── Case B: Company ──────────────────────────────────────────────
    if _has_legal_suffix(payload):
        _set_care_of(res, slot, payload)
        setattr(res, slot, None)
        res.note(15, f"Case B: company routed to care_of ({payload!r})")
        return True

    # ── Case A: Person ───────────────────────────────────────────────
    # Slash-separated payload — pull the leading segment if it looks
    # like a person name, flag the trailing remainder for manual review.
    flagged_slash = False
    candidate = payload
    if "/" in payload:
        before = payload.split("/", 1)[0].strip()
        if before:
            candidate = before
            flagged_slash = True

    has_title = bool(_TITLE_PREFIX_RE.match(candidate))
    verdict_person = (
        candidate.lower() in llm_person_verdicts
        and llm_person_verdicts[candidate.lower()] == "person"
    )
    # 2-3 capitalised words, no org-signal, no job-title tail — when
    # the user has explicitly typed a c/o or ATTN prefix the shape is
    # strong enough evidence on its own (the LLM verdict path remains
    # for borderline values like single-token surnames).
    is_plain_name = (
        bool(_PLAIN_NAME_RE.match(candidate))
        and not _ORG_SIGNAL_RE.search(candidate)
        and not _is_job_title(candidate)
    )

    if has_title or is_plain_name or verdict_person:
        care_of_value = candidate
        contact_value = _strip_title(candidate) or candidate
        if res.contact and res.contact.strip() and res.contact.strip().lower() != contact_value.lower():
            res.note(15, f"Case A: person '{contact_value}' in {slot} but Contact already populated — flag for review")
            res.flags.append("contact-conflict")
        else:
            res.contact = contact_value
        _set_care_of(res, slot, care_of_value)
        setattr(res, slot, None)
        # The department lookup's precondition is an empty Name 2. Clearing a
        # lower slot does not create one, so only Name 2 triggers it.
        if slot == "name2":
            res.trigger_dept_lookup = True
        if flagged_slash:
            res.flags.append(
                f"{slot} had slash-separated payload — only leading person "
                f"name retained ({candidate!r}); remainder needs manual review"
            )
        res.note(15, f"Case A: person routed to care_of+contact ({candidate!r})")
        return True

    # ── Case E: Fallback ─────────────────────────────────────────────
    _set_care_of(res, slot, payload)
    setattr(res, slot, None)
    res.note(15, f"Case E: unclassified payload routed to care_of ({payload!r})")
    return True


def _set_care_of(res: PreprocessResult, slot: str, value: str) -> None:
    """Write *value* to care_of, or flag when a different one is already there.

    With the classifier running across every department slot, two slots can
    each produce a c/o payload. The first one wins — Name 2 is scanned before
    Name 3 — and the loser is reported rather than silently dropped.
    """
    existing = (res.care_of or "").strip()
    if existing and existing.lower() != value.strip().lower():
        res.note(
            15,
            f"c/o payload {value!r} from {slot} discarded — Care Of already "
            f"holds {existing!r}",
        )
        res.flags.append("care-of-conflict")
        return
    res.care_of = value


def _extract_co_attn_from_names(
    res: PreprocessResult,
    llm_person_verdicts: dict[str, str],
) -> set[str]:
    """Run the c/o + ATTN classifier across every department slot.

    Returns the set of slots it handled, so the caller can skip the legacy
    UC 7 Pattern A loop for exactly those fields.
    """
    handled: set[str] = set()
    for slot in DEPT_SLOTS:
        if _extract_co_attn_from_slot(res, slot, llm_person_verdicts):
            handled.add(slot)
    return handled


# ---------------------------------------------------------------------------
# UC 7 — Contact name extraction
# ---------------------------------------------------------------------------

_ATTN_RE = re.compile(
    r"\b(?:attn|att|attention)\b\s*[:\-]?\s*(.+)",
    re.IGNORECASE,
)

# Titles that unambiguously indicate a person.
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(?:Dr\.?|Prof\.?|Professor|Mr\.?|Mrs\.?|Ms\.?|Mx\.?|Sir|"
    r"Ir\.?|Engr\.?|Rev\.?|Hon\.?)\s+[A-Z][\w\-']+(?:\s+[A-Z][\w\-']+){0,3}\s*$",
    re.IGNORECASE,
)

# Signals that the token is an organisation/department, not a person.
_ORG_SIGNAL_RE = re.compile(
    r"\b(?:Inc|Corp|Corporation|Ltd|LLC|LLP|GmbH|AG|SA|Co|Company|"
    r"University|College|Institute|School|Hospital|Centre|Center|"
    r"Department|Dept|Division|Div|Laboratory|Laboratories|Lab|Labs|"
    r"Group|Research|Facility|Facilities|Core|Unit|"
    r"Medical|Clinic|Foundation|Trust|Partners|Associates|"
    r"Services|Systems|Technologies|Sciences|Engineering|"
    r"Office|Desk|Receiving|Shipping|Billing|Accounting|Purchasing|"
    r"Warehouse|Storeroom|Stockroom|Dock|Mailroom|Mail\s*Room)\b",
    re.IGNORECASE,
)

# A bare plain-name pattern: 2-3 capitalised words. A word may carry an
# internal hyphen or apostrophe with a following capital ("Macdonald-Korth",
# "O'Brien", "Smith-Jones") — without that, hyphenated surnames never surface
# for person classification.
_NAME_WORD = r"[A-Z][a-z]+(?:[-'][A-Za-z][a-z]*)*"
# IGNORECASE so an ALL-CAPS SAP export ("ALBERT KAKKIS") is detected as well as
# mixed case; the candidate is title-cased by ``_titlecase_person`` before it
# reaches the classifier / Contact field.
_PLAIN_NAME_RE = re.compile(
    rf"^\s*{_NAME_WORD}\s+(?:[A-Z]\.?\s+)?{_NAME_WORD}(?:\s+{_NAME_WORD})?\s*$",
    re.IGNORECASE,
)


def _titlecase_person(text: str) -> str:
    """Title-case a person name token-wise for the Contact field.

    Only ALL-CAPS or all-lower tokens are re-cased ("ALBERT" → "Albert",
    "F" → "F"); already-mixed-case tokens are preserved so "McIntyre",
    "O'Brien" and "de la Cruz" survive. ``str.title`` handles apostrophes and
    hyphens ("O'BRIEN" → "O'Brien", "SMITH-JONES" → "Smith-Jones"). Person
    names — not org names — so no acronym preservation (unlike smart_title_case).
    """
    if not text:
        return text
    out = []
    for tok in text.split():
        cased = tok.title() if (tok.isupper() or tok.islower()) else tok
        # Restore the internal capital in an "Mc" surname ("MCINTYRE" → title →
        # "Mcintyre" → "McIntyre"). "Mac" is left alone (mangles ordinary names).
        m = re.match(r"^(Mc)([a-z])(.+)$", cased)
        if m:
            cased = f"{m.group(1)}{m.group(2).upper()}{m.group(3)}"
        out.append(cased)
    return " ".join(out)

# Trailing academic / professional credentials that decorate a person name
# ("John Anderson, PhD", "Jane Smith M.D.", "Sam Lee, MSc, MBA"). Stripped
# before person classification so the bare name surfaces.
_CREDENTIALS_RE = re.compile(
    r"(?:,?\s*\b(?:Ph\.?\s*D|D\.?\s*Phil|M\.?\s*D|M\.?\s*S\.?c?|M\.?\s*B\.?\s*A|"
    r"M\.?\s*P\.?\s*H|B\.?\s*S\.?c?|B\.?\s*A|D\.?\s*D\.?\s*S|D\.?\s*V\.?\s*M|"
    r"R\.?\s*N|J\.?\s*D|LL\.?\s*[MB]|P\.?\s*E|C\.?\s*P\.?\s*A|Esq|FACS|FRCP)\.?)+\s*$",
    re.IGNORECASE,
)


def _person_candidate(text: str | None) -> str | None:
    """Normalise a field value to a bare person-name candidate for the LLM
    person/org classifier, or None.

    Strips trailing credentials ("John Anderson, PhD" → "John Anderson") and
    reorders a single "Last, First" comma form ("Smith, John" → "John Smith").
    Returns None when the normalised value carries an organisation signal or is
    not a plain 2-3 word name — so the LLM still makes the final call, but on a
    clean candidate.
    """
    if not text or not text.strip():
        return None
    s = _CREDENTIALS_RE.sub("", text.strip()).strip(" ,;")
    if s.count(",") == 1:
        last, first = (p.strip() for p in s.split(","))
        if last and first and " " not in last and not _ORG_SIGNAL_RE.search(s):
            s = f"{first} {last}"
    s = re.sub(r"\s+", " ", s).strip()
    if not s or _ORG_SIGNAL_RE.search(s):
        return None
    if not _PLAIN_NAME_RE.match(s):
        return None
    return _titlecase_person(s)


_MULTI_CONTACT_SEPARATOR_RE = re.compile(
    r"\s+(?:and|or)\s+|\s*&\s*|[;/]|\s\+\s",
    re.IGNORECASE,
)


def has_multiple_contacts(contact: str | None) -> bool:
    """Return True if *contact* names more than one person.

    Detects strong separators (``and``, ``or``, ``&``, ``;``, ``/``,
    `` + ``). A comma is more ambiguous because ``Last, First`` is a
    legitimate single-person form, so comma triggers a multi-contact
    verdict only when **all** comma-separated parts look like full
    names (>= 2 tokens each).
    """
    if not contact:
        return False
    s = contact.strip()
    if not s:
        return False
    if _MULTI_CONTACT_SEPARATOR_RE.search(s):
        return True
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if len(parts) >= 2 and all(len(p.split()) >= 2 for p in parts):
            return True
    return False


def _strip_contact_trailing_junk(name: str) -> str:
    """Remove phone numbers or trailing organisation tails from an
    extracted contact name."""
    # Strip anything after a slash/pipe/semicolon
    name = re.split(r"[/|;]", name, maxsplit=1)[0]
    # Strip trailing phone patterns
    name = re.sub(r"\s*\(?\+?\d[\d\s\-()]{6,}\d\)?\s*$", "", name)
    return name.strip(" ,.;:-")


def _extract_contact_from_field(text: str, allow_llm: bool = False, llm_client=None) -> tuple[str | None, str, str | None]:
    """Attempt to extract a contact person from *text*.

    Returns ``(contact_or_None, text_after_removal, reason)``.

    Strategy:
      Pattern B1 — starts with a known title (Dr., Prof., ...) and
                   otherwise matches a person-name shape. Deterministic.
      Pattern B2 — a plain 2-3 word capitalised token with NO org
                   signals, NO address, NO email. Optionally asks the
                   LLM to classify person vs organisation.

    Note: Pattern A ("Attn:" prefix) is handled upstream in the main
    preprocess loop with its own org-signal guard, so it is NOT
    re-applied here.
    """
    if not text:
        return None, text, None

    stripped = text.strip()

    # Pattern B1: title prefix + capitalised name. Strip trailing credentials
    # first so "Dr. Jane Smith, PhD" still matches → contact "Dr. Jane Smith".
    core = _CREDENTIALS_RE.sub("", stripped).strip(" ,;")
    if _TITLE_PREFIX_RE.match(core):
        return core, "", "title-prefix"

    # Pattern B2: plain name without title. Only if:
    #   - no org signal words
    #   - matches a 2-3 word capitalised pattern
    #   - allow_llm was enabled
    if (
        allow_llm
        and llm_client is not None
        and not _ORG_SIGNAL_RE.search(stripped)
        and _PLAIN_NAME_RE.match(stripped)
    ):
        try:
            verdict = _llm_classify_person_or_org(llm_client, stripped)
        except Exception as exc:
            logger.info("Preprocess: LLM person-classifier failed: %s", exc)
            verdict = None
        if verdict == "person":
            return stripped, "", "llm-person"

    return None, text, None


def _llm_classify_person_or_org(llm_client, text: str) -> str | None:
    """Return 'person', 'organisation', or None (low confidence).

    Synchronous-style wrapper expecting an async client; caller must
    await the classify. But preprocess is synchronous by design, so
    this path is only invoked from within an async helper. We keep
    the signature sync here and rely on the orchestrator's async
    wrapper to run it.
    """
    raise NotImplementedError(
        "Call _llm_classify_person_or_org_async instead — preprocess is sync"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def preprocess_record(
    name1: str | None,
    name2: str | None,
    name3: str | None,
    contact: str | None,
    email: str | None,
    street1: str | None,
    street2: str | None,
    street3: str | None,
    name4: str | None = None,
    name5: str | None = None,
    street4: str | None = None,
    street5: str | None = None,
    house_number: str | None = None,
    city: str | None = None,
    region: str | None = None,
    llm_person_verdicts: dict[str, str] | None = None,
) -> PreprocessResult:
    """Run all deterministic preprocessing.

    The LLM person classifier (UC 7 Pattern B2) cannot be called
    synchronously from here, so the orchestrator runs an async
    pre-pass over suspicious name fields and passes the verdicts in
    via ``llm_person_verdicts`` (keyed by lowercased text).

    ``city`` / ``region`` are the record's own place, read by UC 12 to decide
    whether a trailing "- <City>, <ST>" on a name names a place at all. They
    are optional: without them the suffix rule keeps only its shape test and
    accepts strictly less (see
    :func:`enrichment.locality.split_site_suffix`).
    """
    res = PreprocessResult(
        name1=name1, name2=name2, name3=name3, name4=name4, name5=name5,
        contact=contact, email=email,
        street1=street1, street2=street2, street3=street3,
        street4=street4, street5=street5,
    )
    llm_person_verdicts = llm_person_verdicts or {}
    # The name block as SUPPLIED, before any router touches it. Read at the
    # end to answer "did this value change slot?", which is what separates a
    # value preprocessing MOVED from one that simply stayed where it was.
    supplied_block = (name1, name2, name3, name4, name5)

    # ---------------------------------------------------------------
    # Leading opaque-code strip. A stray account/customer code prefixed
    # onto a name field ("B800000123 c/o Dr. Mark Adams") is removed so the
    # real content is exposed — in particular so a following c/o clause
    # becomes a prefix that UC 15 can route. Runs FIRST, before UC 15.
    # ---------------------------------------------------------------
    for slot in NAME_SLOTS:
        val = getattr(res, slot)
        stripped = _strip_leading_opaque_code(val)
        if stripped != val:
            setattr(res, slot, stripped or None)
            res.note(10, f"leading opaque code stripped from {slot} (was {val!r})")

    # ---------------------------------------------------------------
    # Collapse a phrase repeated back-to-back within a single field
    # ("Department of Central Receiving Department of Central Receiving"
    # → "Department of Central Receiving"). Applies to name and street slots.
    # ---------------------------------------------------------------
    for slot in (*NAME_SLOTS, *STREET_SLOTS):
        val = getattr(res, slot)
        collapsed = _collapse_repeated_phrase(val)
        if collapsed != val:
            setattr(res, slot, collapsed)
            res.note(12, f"repeated phrase collapsed in {slot} (was {val!r})")

    # ---------------------------------------------------------------
    # Parent organisation + sub-unit. When Name 1 leads with the acronym of a
    # known parent organisation followed by one of its units ("USDA -
    # Kerrville MLRA Office", "NASA Ames Research Center"), the field holds two
    # things: the organisation that owns the record and the unit inside it.
    # Expand the acronym into Name 1 — the organisation slot — and move the
    # unit into the department block, where the dept tiers look for it.
    #
    # Runs BEFORE the dedupe below, which reads any short leading token beside
    # a longer phrase as a restatement of it and deletes the token. That is
    # right when the two name one entity and wrong here: it shipped
    # "Kerrville MLRA Office" for a USDA field office and "Ames Research
    # Center" for a NASA centre, with the owning agency nowhere in the row.
    # ---------------------------------------------------------------
    if res.name1 and res.name1.strip():
        split = _parent_org_split(res.name1)
        if split:
            parent, unit = split
            original = res.name1
            res.name1 = parent
            overflow = _insert_dept_value(res, unit)
            res.note(
                14,
                f"parent organisation expanded into name1 (was {original!r}); "
                f"unit {unit!r} moved to the department block",
            )
            for lost in overflow:
                # The block is full. Say which value was pushed out rather
                # than losing it silently — the unit that prompted the split
                # is the one thing that must not be dropped, so it goes in at
                # the head and whatever sat in the last slot is what reports.
                res.flags.append(
                    f"name-block-overflow: {lost!r} dropped to make room for "
                    f"the unit split out of name1"
                )

    # ---------------------------------------------------------------
    # Name 1 acronym + full-form dedupe. When Name 1 carries BOTH an
    # abbreviation and its expansion for the same entity ("MIT Massachusetts
    # Institute of Technology", "Massachusetts Institute of Technology (MIT)"),
    # keep only the full form.
    # ---------------------------------------------------------------
    # A trailing "- A/P" is the accounts-payable desk, not an abbreviation of
    # the name in front of it: UC 6 below moves it into its own slot, so the
    # dedupe must not read it as a restatement and delete it first.
    if res.name1 and res.name1.strip() and not _split_ap_suffix(res.name1):
        original = res.name1
        deduped = _strip_redundant_acronym(original)
        if deduped and deduped != original:
            res.name1 = deduped
            # When the removed abbreviation was syllabic (not a verified
            # initialism, e.g. "Tuhh - Hamburg University of Technology") the
            # full form is kept but the assumed match is flagged for review.
            syllabic = _syllabic_dash_abbrev(original)
            if syllabic:
                res.flags.append(f"acronym-ambiguous: {syllabic}")
            else:
                res.note(12, f"redundant acronym dropped from name1 (was {original!r})")

    # ---------------------------------------------------------------
    # UC 15 — c/o + ATTN extraction from the department slots (5-case
    # classifier). Runs FIRST so the legacy UC 7 Pattern A loop and UC 6
    # AP normalisation see the already-routed state. Touches Name 2..N,
    # care_of, contact and email only — Name 1 holds the organisation and
    # is never routing text, so it is left alone.
    # ---------------------------------------------------------------
    co_attn_handled = _extract_co_attn_from_names(res, llm_person_verdicts)

    # ---------------------------------------------------------------
    # Named building → Building field. A name OR street slot whose value is a
    # named building ("Neil Armstrong Operations and Checkout Building") is a
    # physical location, not a department or a street line. Move the first
    # such value to the Building output and clear the slot. Name slots are
    # checked first. Runs BEFORE contact/AP/unit logic so the building (which
    # may contain a person-like prefix, e.g. "Neil Armstrong …") is not
    # misread as a contact or department. name1 is left alone — the
    # institution name is not an address field.
    # ---------------------------------------------------------------
    for slot in (*DEPT_SLOTS, *STREET_SLOTS):
        bldg = _named_building(getattr(res, slot))
        if bldg:
            res.building = bldg
            setattr(res, slot, None)
            res.note(9, f"named building moved from {slot} to Building ({bldg!r})")
            break

    # ---------------------------------------------------------------
    # Location fragment → street slot. A name slot that is purely address
    # sub-locations ("Wing C", "Annex D Pod 2") or a bare campus / site label
    # ("Sarasota Campus", "Oxford Science Park") is moved verbatim to the first
    # empty street slot — it says where the organisation sits, not which part
    # of it this record is. Runs before contact/dept logic so the descriptors
    # are not misread as a department.
    # ---------------------------------------------------------------
    for slot in DEPT_SLOTS:
        frag = _location_fragment(getattr(res, slot)) or _site_fragment(getattr(res, slot))
        if not frag:
            continue
        target = _first_empty_street_slot(res)
        if target is None:
            res.flags.append("street-slots-full")
            res.note(9, f"location fragment in {slot} but all street slots full ({frag!r})")
        else:
            setattr(res, target, frag)
            setattr(res, slot, None)
            res.note(9, f"location fragment moved from {slot} to {target} ({frag!r})")

    # ---------------------------------------------------------------
    # Pipe-delimited street that concatenates an org hierarchy with the
    # address ("Dept X | Division Y | ... | U.S. FDA | 5100 Paint Branch Pkwy").
    # Keep the address segment(s) in the street field and route each org /
    # department segment to the first empty Name slot; overflow past the last
    # slot is dropped (redundant hierarchy) and noted. Runs BEFORE the single-value
    # org/dept routers below.
    # ---------------------------------------------------------------
    for slot in STREET_SLOTS:
        split = _split_pipe_street(getattr(res, slot))
        if not split:
            continue
        address, name_parts = split
        setattr(res, slot, address or None)
        _route_org_parts_to_names(res, name_parts, slot)

    # ---------------------------------------------------------------
    # Comma-delimited street that MIXES org/department names with a real
    # address ("Institute ... Chemistry, Faculty of Sustainability,
    # Scharnhorststraße 1 C13.217"). Keep the address segment(s) in the street
    # field and route the org/department (and other non-address) segments to
    # Name slots. Only fires when both an org segment and an address segment
    # are present, so a plain address ("51 Sleeper Street, 7th Floor") is never
    # split. Runs after the pipe split, before the single-value org/dept routers.
    # ---------------------------------------------------------------
    for slot in STREET_SLOTS:
        split = _split_comma_street(getattr(res, slot))
        if not split:
            continue
        address, name_parts, other_parts = split
        setattr(res, slot, address or None)
        _route_org_parts_to_names(res, name_parts, slot)
        # Bare location fragments ("Queens Campus") are kept as street lines in
        # the next empty street slot(s), not routed to a Name field.
        for frag in other_parts:
            target = _first_empty_street_slot(res)
            if target is None:
                if "street-slots-full" not in res.flags:
                    res.flags.append("street-slots-full")
                res.note(16, f"street slots full — fragment left for review: {frag!r}")
                continue
            setattr(res, target, frag)
            res.note(16, f"location fragment {frag!r} from {slot} → {target}")

    # ---------------------------------------------------------------
    # Semicolon-delimited street that puts an org/building label before the
    # address ("UCSF; 600 16th Avenue"). Like the comma split, but the
    # non-address segment may be a bare acronym. Keep the address in the street
    # field and route the org segment(s) to Name slots.
    # ---------------------------------------------------------------
    for slot in STREET_SLOTS:
        split = _split_semicolon_street(getattr(res, slot))
        if not split:
            continue
        address, name_parts, other_parts = split
        setattr(res, slot, address or None)
        _route_org_parts_to_names(res, name_parts, slot)
        for frag in other_parts:
            target = _first_empty_street_slot(res)
            if target is None:
                if "street-slots-full" not in res.flags:
                    res.flags.append("street-slots-full")
                res.note(16, f"street slots full — fragment left for review: {frag!r}")
                continue
            setattr(res, target, frag)
            res.note(16, f"location fragment {frag!r} from {slot} → {target}")

    # ---------------------------------------------------------------
    # Organisation name in a street slot → name block. An institution name
    # that landed in a street field ("University of Miami Hospital") belongs
    # in the names. If the name block has no institution (Name 1 is blank or
    # is only a department), it becomes Name 1 — pushing any department in
    # Name 1 down to the next empty name slot. Otherwise it goes to the first
    # empty name slot. Only the first such street value is moved.
    # ---------------------------------------------------------------
    for slot in STREET_SLOTS:
        val = getattr(res, slot)
        if not _street_is_org_name(val):
            continue
        org = val.strip()
        name1 = (res.name1 or "").strip()
        has_institution = bool(name1) and not is_unit_construction(name1)
        if has_institution:
            target = _first_empty_name_slot(res)
            if target is None:
                continue  # nowhere to put it — leave in street
            setattr(res, target, org)
            res.note(16, f"organisation in {slot} moved to {target} ({org!r})")
        else:
            # Name 1 is empty or only a department → org becomes Name 1.
            if name1:  # a department currently in Name 1 — preserve it
                dept_target = _first_empty_name_slot(res)
                if dept_target:
                    setattr(res, dept_target, name1)
            res.name1 = org
            res.note(16, f"organisation in {slot} promoted to Name 1 ({org!r})")
        _mark_from_street(res, org)
        setattr(res, slot, None)
        break

    # ---------------------------------------------------------------
    # Department in a street slot → name block (or removed). A department /
    # academic unit that landed in a street field ("Department of
    # Neuroscience") is redundant when the name block already has a
    # department/group — drop it. Otherwise move it to the first empty name
    # slot. Processed in order so once one department fills a name slot, any
    # further street departments are treated as redundant.
    # ---------------------------------------------------------------
    for slot in STREET_SLOTS:
        val = getattr(res, slot)
        if not _street_is_department(val):
            continue
        dept = val.strip()
        if _name_block_has_department(res):
            setattr(res, slot, None)
            res.note(16, f"redundant department in {slot} removed ({dept!r})")
        else:
            target = _first_empty_name_slot(res)
            if target is None:
                continue  # no empty name slot — leave it in the street
            # Title-case all-caps input ("CHEMISTRY DEPARTMENT" →
            # "Chemistry Department") so finalise() canonicalises it cleanly
            # to "Department of Chemistry".
            dept_name = _smart_title_case(dept)
            setattr(res, target, dept_name)
            setattr(res, slot, None)
            _mark_from_street(res, dept_name)
            res.note(16, f"department in {slot} moved to {target} ({dept_name!r})")

    # ---------------------------------------------------------------
    # DBA in a street slot → name block. The sibling of the department
    # router above, and for the same reason: a value in a street field that
    # is not an address belongs in the name block, and which slot the export
    # put it in says nothing about what it IS.
    #
    # Record 13333920 arrived with "MCGREGOR LAB" in Street 1, "DBA WACO
    # FAMILY MEDICINE" in Street 2 and the actual address in Street 3. The
    # department router lifted the lab; the DBA line had no router at all and
    # shipped as a street, so Operating Name was empty on a record that states
    # its trading name in so many words.
    #
    # No new judgement: the test is `_normalise_dba`, which UC 11 below
    # already applies to every name slot. Moving the line into a slot is the
    # whole change — UC 11 then normalises the marker and records the slot in
    # `dba_fields`, and the existing Operating Name writer
    # (`orchestrator`, "UC 11 safety net") fills the field at
    # `input:verified+dba`. Every UC 11 protection applies because the value
    # is now in the block UC 11 owns.
    #
    # Runs AFTER the department arm so a record carrying both keeps the
    # department's claim on the first empty slot, exactly as it would have if
    # both had been supplied in the name block.
    # ---------------------------------------------------------------
    for slot in STREET_SLOTS:
        val = getattr(res, slot)
        if not val or not val.strip():
            continue
        _normalised, _is_dba = _normalise_dba(val.strip())
        if not _is_dba:
            continue
        target = _first_empty_name_slot(res)
        if target is None:
            continue  # nowhere to put it — leave it in the street
        # The slot keeps the MARKED string: the marker is what tells UC 11
        # this is a trading name, and `dba_payload` strips it when it writes
        # the operating name. Cased with the name rules rather than the street
        # rules — that is the difference between "DBA" and "Dba".
        _dba_name = _smart_title_case(_normalised)
        setattr(res, target, _dba_name)
        setattr(res, slot, None)
        _mark_from_street(res, _dba_name)
        res.note(16, f"DBA in {slot} moved to {target} ({_dba_name!r})")
        break

    # ---------------------------------------------------------------
    # UC 7 Pattern A (Attn prefix) — runs BEFORE UC 6 so that a field
    # like "Accounts Payable - ATTN: Christina Boske" yields both
    # contact="Christina Boske" AND name2="Accounts Payable". If UC 6
    # ran first it would replace the whole field with "Accounts
    # Payable" and lose the contact information.
    #
    # Guard: the Attn payload must look like a PERSON name. A payload
    # that contains organisation signals (Lab, Dept, Group, Center,
    # Facility, Division, Office, etc.) is a department/desk label,
    # not a contact — leave the field untouched in that case.
    # e.g. "Attn: FISH LAB" → NOT a contact.
    # ---------------------------------------------------------------
    for field_name in NAME_SLOTS:
        # Slots UC 15 above already routed (5-case classifier) are settled.
        if field_name in co_attn_handled:
            continue
        val = getattr(res, field_name)
        if not val:
            continue
        m = _ATTN_RE.search(val)
        if not m:
            continue
        raw = m.group(1).strip()
        cleaned = _strip_contact_trailing_junk(raw)
        if not cleaned:
            continue
        # Reject Attn payloads that look like org/department labels —
        # keep them in the name field (they ARE the department) but
        # strip the leading "Attn:" metadata prefix so the canonical
        # value is just the unit name (e.g. "Attn: Candelario Lab" →
        # "Candelario Lab").
        if _ORG_SIGNAL_RE.search(cleaned):
            # Remove only the Attn prefix, keep the payload.
            remaining_prefix = val[: m.start()]
            new_val = (remaining_prefix + cleaned).strip()
            new_val = re.sub(r"\s+", " ", new_val).strip(" ,;/|-")
            setattr(res, field_name, new_val or None)
            res.flags.append(
                f"attn prefix stripped from {field_name}; payload "
                f"({cleaned!r}) is a department label, not a person"
            )
            continue
        # Remove only the Attn clause from the field, keeping anything
        # that precedes it (so "Accounts Payable - Attn: X" → "Accounts
        # Payable -").
        remaining = (val[: m.start()] + val[m.end():])
        remaining = re.sub(r"\s+", " ", remaining).strip(" ,;/|-")
        if res.contact and res.contact.strip():
            res.note(7, f"contact '{cleaned}' in {field_name} but Contact already populated — flag for review")
            res.flags.append("contact-conflict")
        else:
            res.contact = cleaned
            res.note(7, f"extracted contact from {field_name} (attn-prefix)")
        setattr(res, field_name, remaining or None)

    # ---------------------------------------------------------------
    # UC 6 — Accounts Payable normalisation
    # ---------------------------------------------------------------
    for field_name in NAME_SLOTS:
        val = getattr(res, field_name)
        if not (val and val.strip()):
            continue
        # "McLaren HealthCare Corp/Acct Pay" is an organisation AND a desk in
        # one field. Keep the organisation where it is — it is the only name
        # the record has — and give the desk its own slot. Replacing the whole
        # field, the way a desk-only value is replaced below, would throw the
        # customer away and leave the record with nothing to enrich.
        # The delimited form first, then the same value written without a
        # separator. Both mean "an organisation and a desk in one field", and
        # both keep the organisation where it is.
        organisation = _split_ap_suffix(val) or _trailing_ap_phrase(val)
        if organisation:
            target = _first_empty_name_slot(res)
            if target is None:
                res.note(
                    6,
                    f"accounts-payable desk embedded in {field_name} but "
                    f"{'/'.join(DEPT_SLOTS)} full — left in place",
                )
                continue
            setattr(res, field_name, organisation)
            setattr(res, target, "Accounts Payable")
            res.note(
                6,
                f"split Accounts Payable out of {field_name} to {target} "
                f"(was {val!r})",
            )
            continue
        if _is_ap_reference(val):
            setattr(res, field_name, "Accounts Payable")
            res.note(6, f"{field_name} normalised to Accounts Payable (was {val!r})")

    # ---------------------------------------------------------------
    # UC 8 — Email copy. Scan name and address fields for email addresses,
    # move every one of them to the dedicated email field, and strip them
    # from the source field so the cleaned name/street value is not polluted
    # with a stray address.
    #
    # A SECOND, different address does not displace the first and is no
    # longer left where it was found: `add_email` appends it and the record
    # carries both, with `email-conflict` raised so a steward decides which
    # one the mail is for. Leaving it in place shipped an email in a Name
    # column, where nothing downstream looks for one.
    # ---------------------------------------------------------------
    for field_name in (*NAME_SLOTS, *STREET_SLOTS):
        val = getattr(res, field_name)
        if not val:
            continue
        found = _find_emails(val)
        if not found:
            continue
        for address in found:
            outcome = res.add_email(address)
            if outcome == "conflict":
                res.note(
                    8,
                    f"second email {address!r} found in {field_name} — kept "
                    f"alongside the address already captured, flag for review",
                )
                if "email-conflict" not in res.flags:
                    res.flags.append("email-conflict")
            elif outcome == "new":
                res.note(8, f"copied email from {field_name}")
        # Remove the email token(s) from the source field and tidy residue.
        stripped = _EMAIL_RE.sub("", val)
        stripped = re.sub(r"\s+", " ", stripped).strip(" ,;:/|-")
        # "email invoices to: ap@acme.com" leaves "email invoices to", which
        # is the label and not a value. The address it introduced is in the
        # Email column; the label has nothing left to introduce.
        if _is_email_label_only(stripped):
            res.note(
                8,
                f"{field_name} cleared — routing label with no value left "
                f"(was {val!r})",
            )
            stripped = ""
        if stripped != val:
            setattr(res, field_name, stripped or None)
            if stripped:
                res.note(8, f"removed email from {field_name}")

    # ---------------------------------------------------------------
    # UC 9 — A street line and a delivery instruction sharing one department
    # slot ("Crystalline Dr. Loading Dock"). Both halves are address content
    # and neither is a department: the street goes to a street slot, and the
    # instruction goes to one too, where the address stage's logistics
    # extractor moves it on to Unloading Point. Runs BEFORE the extraction
    # loop below, which cannot see this shape at all — see
    # `_split_street_access_qualifier`. Name 1 is the organisation and is
    # never touched.
    # ---------------------------------------------------------------
    for field_name in DEPT_SLOTS:
        val = getattr(res, field_name)
        split = _split_street_access_qualifier(val)
        if not split:
            continue
        parts = [
            part for part in split
            if not _duplicates_existing_street(part, res, house_number)
        ]
        free = [
            slot for slot in STREET_SLOTS
            if not (getattr(res, slot) or "").strip()
        ]
        if len(free) < len(parts):
            # Nothing moves unless every half has somewhere to go. Splitting a
            # value and placing only one half is the silent loss this block
            # exists to stop, in a second form.
            if "street-slots-full" not in res.flags:
                res.flags.append("street-slots-full")
            res.note(
                9,
                f"street + delivery instruction in {field_name} but too few "
                f"free street slots — left in place ({val!r})",
            )
            continue
        for slot, part in zip(free, parts):
            setattr(res, slot, part)
        setattr(res, field_name, None)
        res.note(
            9,
            f"street + delivery instruction split out of {field_name} "
            f"(was {val!r}) → "
            + ", ".join(f"{s}={p!r}" for s, p in zip(free, parts)),
        )

    # ---------------------------------------------------------------
    # UC 9 — Address extraction
    # ---------------------------------------------------------------
    for field_name in NAME_SLOTS:
        val = getattr(res, field_name)
        if not val:
            continue
        addrs, cleaned = _extract_addresses(val)
        if not addrs:
            continue
        for addr in addrs:
            # Item 5: never write a fragment that duplicates an existing street
            # field (or that field + House Number). "4200 Research Blvd" from a
            # name field is dropped when Street 1 already holds "RESEARCH BLVD"
            # and House Number is "4200".
            if _duplicates_existing_street(addr, res, house_number):
                res.note(9, f"address '{addr}' from {field_name} duplicates an existing street field — discarded")
                continue
            slot = _first_empty_street_slot(res)
            if slot is None:
                res.note(9, f"address '{addr}' found in {field_name} but all street slots full — flag for review")
                res.flags.append("street-slots-full")
                continue
            setattr(res, slot, addr)
            res.note(9, f"extracted address to {slot} from {field_name}")
        setattr(res, field_name, cleaned or None)

    # ---------------------------------------------------------------
    # UC 12 — Bracketed spans dropped from every name slot. A "(Detroit)"
    # / "(United States)" / "(MIT)" tail is a source-system disambiguator,
    # not part of the name, and keeping it splits one organisation across
    # several spellings. Runs AFTER UC 8 (email) and UC 9 (address) so a
    # bracketed email or street is still routed to its own field before
    # the span is discarded, and BEFORE the slot dedupe below so
    # "3M (Detroit)" and "3M" in adjacent slots collapse to one value.
    # ---------------------------------------------------------------
    for field_name in NAME_SLOTS:
        val = getattr(res, field_name)
        if not val:
            continue
        # "(Julius-Maximilians Universität Würzburg) Gieshügeler Str.46" — the
        # brackets LEAD and hold the organisation, so dropping the span would
        # throw the name away and keep the street. Rescue it first: the
        # bracketed org becomes the slot value and the trailing remainder is
        # routed to the block it belongs to.
        led = _leading_bracketed_name(val)
        if led:
            org, rest = led
            setattr(res, field_name, org)
            if _looks_like_name_content(rest):
                target = _first_empty_name_slot(res)
                block = "name"
            else:
                target = _first_empty_street_slot(res)
                block = "street"
            if target is None:
                res.flags.append(f"{block}-slots-full")
                res.note(12, f"leading bracketed org kept in {field_name}; "
                             f"remainder has no free {block} slot ({rest!r})")
            else:
                setattr(res, target, rest)
                res.note(12, f"leading bracketed org kept in {field_name} "
                             f"({org!r}); remainder moved to {target} ({rest!r})")
            continue
        # The dash form of the same disambiguator: "Veracyte, Inc. - South
        # San Francisco, CA", "Merck Research Laboratories - Rahway, NJ". It
        # says which SITE the record is about, which the address block already
        # says, and it costs the name twice over: no registry matches it, and
        # the identity guard reads the canonical form that drops it as naming
        # a DIFFERENT unit — so the record shipped its own input back with
        # "the canonical form could not be established". Dropped BEFORE the
        # parenthetical strip so a value carrying both loses both.
        site = split_site_suffix(val, city=city, region=region)
        if site:
            core, place = site
            setattr(res, field_name, core)
            res.note(
                12,
                f"site qualifier {place!r} dropped from {field_name} "
                f"(was {val!r})",
            )
            _record_site_conflict(res, field_name, place, city, region)
            val = core
        stripped = strip_parentheticals(val)
        if stripped != val:
            setattr(res, field_name, stripped or None)
            res.note(12, f"parenthetical dropped from {field_name} (was {val!r})")

    # ---------------------------------------------------------------
    # UC 11 — DBA normalisation (rewrite any variant to "DBA")
    # ---------------------------------------------------------------
    for field_name in NAME_SLOTS:
        val = getattr(res, field_name)
        if not val:
            continue
        normalised, changed = _normalise_dba(val)
        if changed:
            setattr(res, field_name, normalised or None)
            res.dba_fields.add(field_name)
            res.note(11, f"{field_name} DBA variant normalised to 'DBA' (was {val!r})")

    # ---------------------------------------------------------------
    # UC 17 — Long-form legal-suffix normalisation. Collapse
    # "Aktiengesellschaft" → "AG", "Incorporated" → "Inc", etc. so a
    # company name resolves on the same path regardless of which legal
    # form the source system recorded.
    # ---------------------------------------------------------------
    for field_name in NAME_SLOTS:
        val = getattr(res, field_name)
        if not val:
            continue
        normalised, changed = _normalise_legal_suffix(val)
        if changed:
            setattr(res, field_name, normalised or None)
            res.note(17, f"{field_name} legal suffix normalised (was {val!r})")

    # ---------------------------------------------------------------
    # UC 10 — Opaque code / meaningless identifier clearing
    # ---------------------------------------------------------------
    for field_name in DEPT_SLOTS:
        val = getattr(res, field_name)
        if val and _is_opaque_code(val):
            res.note(10, f"{field_name} cleared — opaque code {val!r}")
            setattr(res, field_name, None)

    # ---------------------------------------------------------------
    # UC 7 — Contact extraction
    # ---------------------------------------------------------------
    for field_name in NAME_SLOTS:
        val = getattr(res, field_name)
        if not val:
            continue

        # Pattern A + B1 (deterministic)
        extracted, remaining, reason = _extract_contact_from_field(val)

        # Pattern B2: check LLM verdict if available — against the raw value,
        # then against a normalised person candidate (credentials stripped,
        # "Last, First" reordered) so "Smith, John" and "John Anderson, PhD"
        # are extracted too.
        if not extracted and val.strip().rstrip(",").strip().lower() in llm_person_verdicts:
            if llm_person_verdicts[val.strip().rstrip(",").strip().lower()] == "person":
                # Title-case the raw (possibly ALL-CAPS) value for the Contact field.
                extracted = _titlecase_person(val.strip().rstrip(",").strip())
                remaining = ""
                reason = "llm-person"
        if not extracted:
            cand = _person_candidate(val)
            if cand and llm_person_verdicts.get(cand.lower()) == "person":
                extracted = cand
                remaining = ""
                reason = "llm-person-normalised"

        # Defensive: never extract an Accounts Payable reference as a
        # contact, regardless of which pattern matched. The AP detector
        # catches "Accounts Payable", "A/P", "Accts Payable" and similar
        # variants that are department labels, not people.
        if extracted and _is_ap_reference(extracted):
            extracted = None
            remaining = val
            reason = None

        if extracted:
            if res.contact and res.contact.strip():
                res.note(7, f"contact '{extracted}' in {field_name} but Contact already populated — flag for review")
                res.flags.append("contact-conflict")
            else:
                res.contact = extracted
                res.note(7, f"extracted contact from {field_name} ({reason})")
            setattr(res, field_name, remaining or None)
            # Name 1 held only a person → signal the affiliation lookup so the
            # institution + department can be fetched from the web.
            if field_name == "name1" and not (remaining and remaining.strip()):
                res.name1_was_person = True

    # ---------------------------------------------------------------
    # UC 16 — Split an institution + embedded department in Name 1.
    # "University of Miami Department of Chemistry" → name1="University
    # of Miami", name2="Department of Chemistry". Only fires when the
    # prefix carries an institution keyword, so government agencies
    # ("United States Department of Agriculture", "Florida Department of
    # Health") are left intact. The department goes to the first empty
    # name slot; if none is free, Name 1 is left unchanged and flagged.
    # ---------------------------------------------------------------
    if res.name1 and res.name1.strip():
        m = _DEPT_PHRASE_RE.search(res.name1)
        if m:
            prefix = res.name1[: m.start()].strip(" ,;-")
            dept = m.group(1).strip(" ,;-")
            if prefix and _INSTITUTION_PREFIX_RE.search(prefix):
                target = _first_empty_name_slot(res)
                if target:
                    setattr(res, target, dept)
                    res.name1 = prefix
                    res.split_values.append(dept)
                    res.note(16, f"split department {dept!r} from name1 to {target}")
                else:
                    res.flags.append("name1-embedded-department")
                    res.note(
                        16,
                        f"department {dept!r} embedded in name1 but "
                        f"{'/'.join(DEPT_SLOTS)} full",
                    )

    # ---------------------------------------------------------------
    # UC 13 — department-slot residual junk cleanup. After the structured
    # extractors above, any slot below Name 1 can still carry stray URLs,
    # phone/fax numbers, or opaque codes. Strip them so every unit value is
    # clean. Runs before UC 14 so a junk-only slot is not promoted upward.
    # ---------------------------------------------------------------
    for slot in DEPT_SLOTS:
        val = getattr(res, slot)
        if not (val and val.strip()):
            continue
        cleaned = _strip_dept_slot_junk(val)
        if cleaned != val:
            res.note(13, f"{slot} junk stripped (was {val!r})")
            setattr(res, slot, cleaned)

    # Same residual cleanup for the street slots — URLs and phone/fax
    # numbers only (opaque codes are left alone so street numbers survive).
    for slot in STREET_SLOTS:
        val = getattr(res, slot)
        if not (val and val.strip()):
            continue
        # A person sitting in a street slot → move to Contact, clear slot.
        person = _street_person_name(val)
        if person:
            if res.contact and res.contact.strip():
                res.note(13, f"person {person!r} in {slot} but Contact already populated — flag for review")
                res.flags.append("contact-conflict")
            else:
                res.contact = person
                res.note(13, f"moved person from {slot} to contact ({person!r})")
                setattr(res, slot, None)
                continue
        cleaned = _strip_street_junk(val)
        if cleaned != val:
            res.note(13, f"{slot} junk stripped (was {val!r})")
            setattr(res, slot, cleaned)

    # ---------------------------------------------------------------
    # UC 14 — Name-slot consolidation.
    # Close any gap in the department block by packing it leftward, so a
    # value in a lower slot moves up into the first empty one above it.
    # The dept-lookup logic (Tier 1 ROR child match, Tier 2 canonical)
    # operates on name2; a record with the dept in name3 (or name5) and
    # name2 blank would skip those tiers entirely. Move the value into the
    # slot the pipeline expects.
    # Runs after UC 7/8/9 so a slot cleared by contact/email/address
    # extraction is not promoted, and before UC 12 so dedup sees the
    # post-shift state.
    # ---------------------------------------------------------------
    # When Name 1 was ONLY a person (now moved to Contact) and the street→name
    # routing had to park the organisation in Name 2 (because Name 1 was still
    # occupied by the person at routing time), promote that organisation into
    # the now-empty Name 1 — the org, not the person's absence, must drive
    # enrichment. Gated so a bare department is left in place (a person + dept
    # record with no institution still flags for a lookup rather than treating
    # the department as the institution).
    if (
        res.name1_was_person
        and not (res.name1 and res.name1.strip())
        and res.name2 and res.name2.strip()
        and (_looks_like_institution(res.name2) or _looks_like_org_acronym(res.name2))
    ):
        res.note(14, f"organisation promoted from name2 to empty name1 after person extraction ({res.name2!r})")
        res.name1 = res.name2
        res.name2 = None

    # Pack the dept slots (every slot below Name 1) leftward so any gap left
    # by a cleared field is closed and the dept lands in name2 where the
    # tiers look for it. With only name3 populated this reduces to the
    # original name3 -> name2 shift.
    current = [
        (v.strip() if (v := getattr(res, slot)) and v.strip() else None)
        for slot in DEPT_SLOTS
    ]
    packed = [v for v in current if v]
    packed += [None] * (len(DEPT_SLOTS) - len(packed))
    if current != packed:
        before = ", ".join(
            f"{slot}={getattr(res, slot)!r}" for slot in DEPT_SLOTS
        )
        res.note(14, f"name slots packed leftward ({before})")
        for slot, value in zip(DEPT_SLOTS, packed):
            setattr(res, slot, value)

    # ---------------------------------------------------------------
    # UC 12 — Identical duplicate name fields cleared silently.
    # Runs LAST so that it sees the post-normalisation values
    # (e.g. name1 and name2 both collapsed to "Accounts Payable" by
    # UC 6 will be deduped here). Lower slots are evaluated before higher
    # ones so an all-equal case collapses cleanly: ("A","A","A") → name3
    # cleared first, then name2 cleared.
    # No flag is added — duplicate clearing is informational only.
    # ---------------------------------------------------------------
    # Compare on the CANONICAL unit form so surface variants of the same
    # department are recognised as duplicates ("Department of Main Receiving"
    # == "Main Receiving Department" == "Main Receiving Dept").
    def _norm(v: str | None) -> str:
        if not v or not v.strip():
            return ""
        canon = canonicalise_unit_name(v) or v
        return re.sub(r"\s+", " ", canon.strip()).lower()

    # Equivalent when the canonical forms match exactly OR are near-identical
    # (a typo / one-off character difference, e.g. "Department of Main
    # Receiving" vs "Department of Main Receivingt"). The 92 threshold is
    # strict enough that genuinely different units ("Physics" vs "Physiology")
    # are not merged.
    def _equiv(a: str | None, b: str | None) -> bool:
        na, nb = _norm(a), _norm(b)
        if not na or not nb:
            return False
        return na == nb or fuzz.ratio(na, nb) >= 92

    # Clear later slots first so an all-equal case collapses cleanly:
    # every slot is compared against every slot above it, walking the block
    # from the bottom up, so ("A","A","A") clears name3 then name2 and
    # leaves name1 holding the single value.
    for i in range(len(NAME_SLOTS) - 1, 0, -1):
        lower = NAME_SLOTS[i]
        lower_val = getattr(res, lower)
        if not lower_val:
            continue
        for upper in NAME_SLOTS[:i]:
            upper_val = getattr(res, upper)
            if upper_val and _equiv(upper_val, lower_val):
                res.note(
                    12,
                    f"{lower} cleared — duplicate of {upper} ({lower_val!r})",
                )
                setattr(res, lower, None)
                break

    # ---------------------------------------------------------------
    # Resolve the street-sourced VALUES the routers recorded to the slots that
    # ended up holding them. Last, because everything above — the person /
    # organisation promotion, UC 14's leftward pack, UC 12's dedupe — can move
    # a value into a different slot or drop it entirely, and a value that no
    # longer sits anywhere in the block must not mark a slot.
    # ---------------------------------------------------------------
    if res.street_sourced_values:
        sourced = {v.lower() for v in res.street_sourced_values}
        for slot in NAME_SLOTS:
            val = getattr(res, slot)
            if val and re.sub(r"\s+", " ", val.strip()).lower() in sourced:
                res.names_from_street.add(slot)

    # ---------------------------------------------------------------
    # Slot origins, resolved the same way and for the same reason: what a slot
    # holds is only settled once every router, the UC 14 pack and the UC 12
    # dedupe have run. Three answers, in the order that makes them
    # distinguishable — a value can be both split off Name 1 and then moved by
    # the pack, and the SPLIT is the more specific fact about where it came
    # from.
    # ---------------------------------------------------------------
    def _flat(value: str | None) -> str:
        return re.sub(r"\s+", " ", (value or "").strip()).lower()

    supplied_at = {
        _flat(v): slot
        for slot, v in zip(NAME_SLOTS, supplied_block)
        if _flat(v)
    }
    split = {_flat(v) for v in res.split_values}
    for slot in NAME_SLOTS:
        flat = _flat(getattr(res, slot, None))
        if not flat:
            continue
        if slot in res.names_from_street:
            res.slot_origins[slot] = "preprocess:street"
        elif flat in split:
            res.slot_origins[slot] = "preprocess:split"
        elif supplied_at.get(flat) not in (None, slot):
            # The record stated this value, in a DIFFERENT slot. The UC 14
            # pack, the person/organisation promotion or the dedupe moved it.
            res.slot_origins[slot] = "preprocess:moved"

    # ---------------------------------------------------------------
    # Phase 4 — the department block's authority, at preprocessing's exit.
    #
    # UC 16 keeps placing a split department into the first empty slot; what
    # changes is that the placement is no longer the last word on it. The
    # Wayne State row states "Wayne State University Dept of Biologica" /
    # "Dept of Biological Sciences" / "Greenberg Lab": UC 16 splits the
    # truncated "Dept of Biologica" out of Name 1 into the first free slot,
    # and the block then holds one unit twice — once complete, once cut off by
    # the field width. Neither UC 12's dedupe nor finalise's saw it, because
    # the two spellings score 82 against each other.
    #
    # Runs after UC 12, the last block-mutating step, so the authority sees
    # the block the record will carry into the tiers rather than an
    # intermediate one.
    # ---------------------------------------------------------------
    _dept_slots = NAME_SLOTS[1:]
    _block = [getattr(res, slot, None) for slot in _dept_slots]
    _origins = [
        res.slot_origins.get(slot, dept_block.ORIGIN_INPUT)
        for slot in _dept_slots
    ]
    _settled, _settled_origins, _log = dept_block.normalise(_block, _origins)
    if _settled != _block:
        # Only the slots that actually moved. Writing every slot back — even
        # unchanged — makes preprocessing report a rewrite it did not perform,
        # and the orchestrator turns that into a `preprocess:value-rewritten`
        # event: four records gained a Name 2 provenance and two a flag,
        # without a single department slot changing.
        for _slot, _before, _after in zip(_dept_slots, _block, _settled):
            if _before != _after:
                setattr(res, _slot, _after)
        for _slot, _origin, _value in zip(
            _dept_slots, _settled_origins, _settled,
        ):
            if _value is None:
                res.slot_origins.pop(_slot, None)
            elif _slot in res.slot_origins or _origin != dept_block.ORIGIN_INPUT:
                res.slot_origins[_slot] = _origin
        # The street mark names a SLOT, and the authority may have moved a
        # value out of a marked one. Re-resolved from the values, exactly as
        # it was resolved above, so a mark never names the wrong value.
        if res.street_sourced_values:
            _sourced = {v.lower() for v in res.street_sourced_values}
            res.names_from_street = {
                slot for slot in NAME_SLOTS
                if (val := getattr(res, slot, None))
                and re.sub(r"\s+", " ", val.strip()).lower() in _sourced
            }
        for _entry in _log:
            res.note(
                12,
                f"dept block {_entry['step']}: {_entry['value']!r} "
                f"({_entry['reason']})",
            )

    return res


def _first_empty_street_slot(res: PreprocessResult) -> str | None:
    for slot in STREET_SLOTS:
        if not getattr(res, slot):
            return slot
    return None


def _first_empty_name_slot(res: PreprocessResult) -> str | None:
    for slot in DEPT_SLOTS:
        val = getattr(res, slot)
        if not (val and val.strip()):
            return slot
    return None


def _mark_from_street(res: PreprocessResult, value: str | None) -> None:
    """Record that *value* was fetched out of a street field.

    The name block is where a name someone typed lives; a value that reaches it
    through one of the street→name routers is only there because the pipeline
    put it there, and it still carries the address line's own wording and
    casing. Downstream that difference decides how the value may be rewritten —
    see :func:`enrichment.orchestrator.finalise`, which canonicalises a
    street-sourced unit name that UC 5 would otherwise leave exactly as
    supplied.
    """
    v = re.sub(r"\s+", " ", value.strip()) if value and value.strip() else ""
    if v:
        res.street_sourced_values.append(v)


def _street_is_org_name(value: str | None) -> bool:
    """True when a STREET value is actually an organisation/institution name
    ("University of Miami Hospital"), not an address.

    Requires an organisation signal (research-institution wording or a legal
    company suffix) AND that the value is not address-like (no house-number +
    street-type pattern, no leading street number).
    """
    if not value or not value.strip():
        return False
    v = value.strip()
    if re.match(r"^\s*\d", v):           # leading house number → address
        return False
    if _CO_ATTN_PREFIX_RE.match(v):      # c/o line → care-of content, not an org
        return False
    if is_logistics_location(v):         # distribution centre etc. → not an org
        return False
    addrs, _ = _extract_addresses(v)     # matches a street pattern → address
    if addrs:
        return False
    if _UK_POSTCODE_RE.search(v):        # a UK postcode → address, not an org
        return False
    # "University Road" / "College Street" is a street name, not an institution —
    # mask those before testing for research-institution wording.
    masked = _mask_street_institution_words(v)
    return looks_like_research_institution(masked) or bool(_LEGAL_SUFFIX_RE.search(v))


# Functional business units ("Finance", "Procurement") that name a desk, not an
# address. Recognised even slash/comma/and-separated ("Finance/Procurement").
_FUNCTIONAL_UNIT_WORDS = {
    "finance", "procurement", "accounting", "purchasing", "payroll", "legal",
    "logistics", "operations", "marketing", "sales", "administration", "admin",
    "hr", "human resources", "accounts payable", "accounts receivable",
    "it", "information technology", "shipping", "receiving",
}


def _is_functional_dept(value: str | None) -> bool:
    """True when a value is only functional-unit words, optionally slash/comma/
    and-separated ("Finance/Procurement", "Finance & Procurement")."""
    if not value or not value.strip():
        return False
    parts = re.split(r"[/,&]|\band\b", value.strip(), flags=re.IGNORECASE)
    parts = [p.strip().lower() for p in parts if p.strip()]
    return bool(parts) and all(p in _FUNCTIONAL_UNIT_WORDS for p in parts)


def _street_is_department(value: str | None) -> bool:
    """True when a STREET value is actually a department / academic unit
    ("Department of Neuroscience", "Smith Lab"), not an address.

    A logistics facility ("Southeast Distribution Ctr") is excluded — it
    reads as a "...Center" unit but is an unloading point, not a department.
    """
    if not value or not value.strip():
        return False
    v = value.strip()
    if re.match(r"^\s*\d", v):
        return False
    if is_logistics_location(v):
        return False
    addrs, _ = _extract_addresses(v)
    if addrs:
        return False
    # "Accounts Payable" / "A/P Dept" is an accounting desk, not an address —
    # route it to a Name slot (Name 2) like any other department.
    return (
        is_unit_construction(v)
        or is_granular_unit(v)
        or _is_ap_reference(v)
        or _is_functional_dept(v)
    )


def _split_pipe_street(value: str | None) -> tuple[str | None, list[str]] | None:
    """Route the ORGANISATION/DEPARTMENT segments of a pipe-delimited street to
    the Name block, classifying each segment individually.

    SAP exports sometimes dump an org hierarchy, a c/o line, a building and the
    address into one pipe-separated Street field:

        "MRC - Medical Research Council | C/O RCUK SSC Ltd | Polaris House |
         North Star House | North Star Avenue"

    Only org/department segments move to the Name block. Every other kind of
    segment — a c/o line, a named building, the street address, a campus
    fragment — is kept in the street field (pipe separators dropped) for the
    late address stage (``process_address``) to place into its own output field
    per the scope table. This is the correct direction: earlier the splitter
    routed an unsplit blob to a Name slot and left the org name in the street.

    Returns ``(street_remainder, org_parts)`` — ``street_remainder`` is the kept
    segments rejoined with ", " (or None), ``org_parts`` the org/department
    segments in order. Returns None when no segment is an org/department (a
    plain multi-line address keeps its pipes for ``process_address`` to strip).
    """
    if not value or "|" not in value:
        return None
    parts = [p.strip(" ,;-") for p in value.split("|")]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return None
    org_parts: list[str] = []
    keep_parts: list[str] = []
    for part in parts:
        if _CO_ATTN_PREFIX_RE.match(part) or _named_building(part):
            # c/o line or a named building → leave for the address stage.
            keep_parts.append(part)
        elif _looks_like_name_content(part):
            org_parts.append(part)
        else:
            # Address, campus/site fragment, mail code, bare residue.
            keep_parts.append(part)
    if not org_parts:
        return None
    street_remainder = ", ".join(keep_parts) if keep_parts else None
    return street_remainder, org_parts


# A German-style street ("Scharnhorststraße 1", "Hauptstr. 5", "Lindenallee 12").
_GERMAN_STREET_RE = re.compile(
    r"\b[\wäöüÄÖÜß\-]+(?:stra(?:ße|sse)|str\.?|weg|platz|allee|gasse|ring|damm)\b\s+\d+",
    re.IGNORECASE,
)
# A US "ST 12345" state+zip tail.
_STATE_ZIP_RE = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")
# A sub-location segment ("7th Floor", "Room F107", "Suite 400", "Unit 3").
_SUBLOC_SEG_RE = re.compile(
    r"\b(?:Floor|Fl|Room|Rm|Suite|Ste|Unit|Bldg|Building|Mail\s*Stop|MS|"
    r"P\.?\s*O\.?\s*Box|Box)\b",
    re.IGNORECASE,
)
# A UK postcode ("BT7 1NH", "SW1A 1AA", "M1 1AE", "EC1A 1BB") — an address tail.
_UK_POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s+\d[A-Z]{2}\b", re.IGNORECASE)
# An institution keyword used as a STREET-NAME modifier ("University Road",
# "College Street", "Church Ave") is a STREET, not an institution — so the org
# detectors must not read it as one.
_INSTITUTION_STREET_RE = re.compile(
    r"\b(?:University|College|Institute|Hospital|Church|Academy|School)\s+"
    r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|"
    r"Way|Hwy|Highway|Pl|Place|Pkwy|Parkway|Ct|Court|Ter|Terrace|"
    r"Cir|Circle|Sq|Square)\b",
    re.IGNORECASE,
)


def _mask_street_institution_words(value: str) -> str:
    """Blank out institution keywords that are really street-name modifiers
    ("University Road") so the org detectors don't read a street as an org."""
    return _INSTITUTION_STREET_RE.sub(" ", value)


def _segment_is_address(seg: str) -> bool:
    """True when a comma-segment looks like address content (US or German
    street, US state+zip or UK postcode tail, or a numbered sub-location)."""
    if not seg or not seg.strip():
        return False
    addrs, _ = _extract_addresses(seg)
    if addrs:
        return True
    if _GERMAN_STREET_RE.search(seg):
        return True
    if _STATE_ZIP_RE.search(seg):
        return True
    if _UK_POSTCODE_RE.search(seg):
        return True
    if _SUBLOC_SEG_RE.search(seg) and any(c.isdigit() for c in seg):
        return True
    return False


# A sub-unit / department (either led by a unit word + of/for, or trailing one).
_DEPT_LEAD_RE = re.compile(
    r"^(?:the\s+)?(?:Division|Office|Department|Dept|Center|Centre|Faculty|Branch|"
    r"School|Section|Unit|Group|Laborator(?:y|ies)|Lab|Program|Programme|Bureau|"
    r"Directorate)\b",
    re.IGNORECASE,
)
_DEPT_TRAILING_RE = re.compile(
    r"\b(?:Branch|Division|Office|Section|Unit|Group|Laborator(?:y|ies)|Lab)\s*$",
    re.IGNORECASE,
)
# "Lab 576" / "Lab A12" — a room identifier (Lab + a numeric id), NOT a
# department. Distinguished from "Smith Lab" (a named lab) by the trailing id.
_LAB_ROOM_RE = re.compile(r"^Lab\.?\s+\w*\d[\w\-]*$", re.IGNORECASE)
# Top-level institution keywords (an institution, not a sub-unit of one).
_INSTITUTION_RE = re.compile(
    r"\b(?:University|Universit[äa]t|College|Administration|Agency|Ministry|"
    r"Corporation|Company|Hospital|Clinic|Institute|Institut|Foundation|Society)\b",
    re.IGNORECASE,
)


def _looks_like_institution(seg: str) -> bool:
    """True when a segment names a top-level institution (→ Name 1), not a
    department/sub-unit. "U.S. Food and Drug Administration" and "Institute of
    X" are institutions; "Division of X", "Center for Y", "... Branch" are not.
    """
    if not seg or not seg.strip():
        return False
    if _DEPT_LEAD_RE.match(seg) or _DEPT_TRAILING_RE.search(seg):
        return False
    masked = _mask_street_institution_words(seg)
    return bool(_INSTITUTION_RE.search(masked) or looks_like_research_institution(masked))


def _looks_like_name_content(seg: str) -> bool:
    """True when a segment is org/department/institution content that belongs in
    a Name slot (as opposed to an address or a bare location fragment)."""
    if not seg or not seg.strip():
        return False
    # A c/o line, a named building, or a "Lab 576" room code is address-stage
    # content, never a Name.
    if _CO_ATTN_PREFIX_RE.match(seg) or _named_building(seg) or _LAB_ROOM_RE.match(seg.strip()):
        return False
    return (
        _segment_is_org(seg)
        or _looks_like_institution(seg)
        or bool(_DEPT_LEAD_RE.match(seg))
        or bool(_DEPT_TRAILING_RE.search(seg))
    )


def _segment_is_org(seg: str) -> bool:
    """True when a comma-segment is a recognised organisation / department /
    accounting desk — the trigger signal for splitting a mixed street value."""
    if not seg or not seg.strip():
        return False
    # A c/o line ("C/O RCUK SSC Ltd") carries a legal suffix but is a care-of
    # address, not an organisation — leave it for the address stage.
    if _CO_ATTN_PREFIX_RE.match(seg):
        return False
    masked = _mask_street_institution_words(seg)
    return (
        looks_like_research_institution(masked)
        or is_unit_construction(seg)
        or is_granular_unit(seg)
        or bool(_LEGAL_SUFFIX_RE.search(seg))
        or _is_ap_reference(seg)
    )


def _route_org_parts_to_names(res: "PreprocessResult", parts: list[str], slot: str) -> None:
    """Route org/department segments to the Name block. The institution (if any)
    always takes Name 1 when Name 1 is empty; the remaining segments fill the
    next empty Name slots in order. Overflow past the last slot is dropped
    and noted.
    """
    # Strip a redundant acronym dash form on each routed segment ("MRC -
    # Medical Research Council" → "Medical Research Council"). The name1 acronym
    # dedupe pass has already run by the time a street segment lands in a Name
    # slot, so apply it here too.
    remaining = [(_strip_redundant_acronym(p) or p) for p in parts]
    if remaining and not (res.name1 and res.name1.strip()):
        inst_idx = next(
            (i for i, p in enumerate(remaining) if _looks_like_institution(p)),
            None,
        )
        idx = inst_idx if inst_idx is not None else 0
        res.name1 = remaining.pop(idx)
        _mark_from_street(res, res.name1)
        res.note(16, f"institution from {slot} → name1 ({res.name1!r})")
    for part in remaining:
        target = _first_empty_name_slot(res)
        if target is None:
            # No empty Name slot — do NOT silently drop. Raise the same
            # slots-full flag the orchestrator turns into flag_for_review
            # (see orchestrator's preprocessing-flag handling).
            if "name-slots-full" not in res.flags:
                res.flags.append("name-slots-full")
            res.note(16, f"name slots full — segment left for review: {part!r}")
            continue
        setattr(res, target, part)
        _mark_from_street(res, part)
        res.note(16, f"street segment {part!r} from {slot} → {target}")


def _split_comma_street(
    value: str | None,
) -> tuple[str | None, list[str], list[str]] | None:
    """Split a comma-delimited street value that mixes org/department names
    with a real address, e.g.

        "Institute of ... Chemistry, Faculty of Sustainability,
         Scharnhorststraße 1 C13.217"
        "Room number: F107, Wolfson Research Institute, Queens Campus"

    Returns ``(address, name_parts)`` where address is the address segment(s)
    rejoined (kept in the street field) and name_parts are the org/department
    (and other non-address) segments in order (routed to Name slots).

    Only fires when the value contains BOTH a recognised org/department
    segment AND a recognised address segment — a plain address ("51 Sleeper
    Street, 7th Floor", "200 Clarendon Street, Boston, MA 02210") has no org
    segment and is therefore left untouched.
    """
    if not value or "," not in value:
        return None
    segments = [s.strip(" ;-") for s in value.split(",")]
    segments = [s for s in segments if s]
    if len(segments) < 2:
        return None
    if not any(_segment_is_org(s) for s in segments):
        return None
    # Fire when an org coexists with an address OR a named building (a value
    # like "ICB&DD ... Laboratory, Lab 576, Chemistry Bldg" has no street at all
    # but still needs the org routed to a Name and the building/room left for
    # the address stage). A plain address ("51 Sleeper Street, 7th Floor") has
    # no org segment and is left untouched.
    if not any(_segment_is_address(s) or _named_building(s) for s in segments):
        return None
    addr_parts: list[str] = []
    name_parts: list[str] = []
    other_parts: list[str] = []
    for seg in segments:
        if _segment_is_address(seg):
            addr_parts.append(seg)
        elif _looks_like_name_content(seg):
            name_parts.append(seg)
        else:
            # Neither address nor org — a named building ("Chemistry Bldg"), a
            # room code ("Lab 576"), or a bare location fragment ("Queens
            # Campus"). Kept as a street line for the address stage to place.
            other_parts.append(seg)
    if not name_parts or (not addr_parts and not other_parts):
        return None
    address = ", ".join(addr_parts) if addr_parts else None
    return address, name_parts, other_parts


# A bare institution acronym as a street segment ("UCSF", "MIT", "UCLA", "NIH").
_ORG_ACRONYM_RE = re.compile(r"^[A-Z]{2,6}$")


def _looks_like_org_acronym(seg: str) -> bool:
    """True for a bare institution acronym ("UCSF"). Used only for the
    semicolon splitter, where a short all-caps token before an address is
    reliably an organisation label rather than an address line."""
    return bool(seg and _ORG_ACRONYM_RE.match(seg.strip()))


def _split_semicolon_street(
    value: str | None,
) -> tuple[str | None, list[str], list[str]] | None:
    """Split a semicolon-delimited street that puts an organisation/building
    label before the address, e.g. "UCSF; 600 16th Avenue".

    Semicolons in these SAP street dumps reliably separate an org label from
    the address, so the non-address segment can be a bare acronym ("UCSF") that
    the comma splitter would not recognise as an org. Returns
    ``(address, name_parts, other_parts)`` like ``_split_comma_street``. Fires
    only when a segment is address-like AND another is name content or an
    institution acronym — a semicolon between two address lines is untouched.
    """
    if not value or ";" not in value:
        return None
    segments = [s.strip(" ,-") for s in value.split(";")]
    segments = [s for s in segments if s]
    if len(segments) < 2:
        return None
    if not any(_segment_is_address(s) for s in segments):
        return None
    addr_parts: list[str] = []
    name_parts: list[str] = []
    other_parts: list[str] = []
    for seg in segments:
        if _segment_is_address(seg):
            addr_parts.append(seg)
        elif _looks_like_name_content(seg) or _looks_like_org_acronym(seg):
            name_parts.append(seg)
        else:
            other_parts.append(seg)
    if not addr_parts or not name_parts:
        return None
    return ", ".join(addr_parts), name_parts, other_parts


def _name_block_has_department(res: PreprocessResult) -> bool:
    """True when a DEPARTMENT slot already holds a department / group / unit.

    Only Name 2..N are checked — Name 1 is the institution slot, and many
    institution names ("School of Medicine", "Scripps Research Institute",
    "Moffitt Cancer Center") read as unit/granular constructions; counting
    them here would wrongly drop a real department found in a street field.
    """
    for slot in DEPT_SLOTS:
        val = getattr(res, slot)
        if val and (is_unit_construction(val) or is_granular_unit(val)):
            return True
    return False


# Title-casing of ALL-CAPS names now lives in utils.text_utils so the
# orchestrator's passthrough cleanup and preprocessing share one implementation.
_smart_title_case = smart_title_case


# ---------------------------------------------------------------------------
# Async helpers used by the orchestrator
# ---------------------------------------------------------------------------

def find_suspicious_plain_names(*names: str | None) -> list[str]:
    """Return distinct plain-name candidates that need LLM classification.

    A candidate is a value that:
      - has no known title prefix (Dr., Prof., ...),
      - matches the 2-3 word capitalised pattern,
      - contains NO organisation signal words.

    *names* are the name-block values in slot order (Name 1 first).

    Every department slot — every slot below Name 1 — is read through the
    c/o / ATTN prefix stripper, so payloads like "Attn: Victoria Babinetz"
    surface "Victoria Babinetz" as a candidate for UC 15 Case A wherever
    they were entered. UC 15 itself runs across the same slots, so the two
    have to agree on which fields can carry a routing prefix.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _maybe_add(candidate: str) -> None:
        if not candidate:
            return
        # Strip a trailing comma ("JOHN F FLOREK," → "JOHN F FLOREK") before the
        # shape checks so a stray SAP comma doesn't block detection.
        candidate = candidate.strip().rstrip(",").strip()
        if not candidate:
            return
        if _TITLE_PREFIX_RE.match(candidate):
            return
        if _ORG_SIGNAL_RE.search(candidate):
            return
        if not _PLAIN_NAME_RE.match(candidate):
            return
        # Title-case ALL-CAPS / all-lower candidates before the LLM sees them
        # and before they key the verdict map (so the Contact write is clean).
        normalised = _titlecase_person(candidate)
        key = normalised.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(normalised)

    for field_name, val in zip(NAME_SLOTS, names):
        if not val:
            continue
        stripped = val.strip()
        if not stripped:
            continue
        # Strip c/o / ATTN prefix and parenthetical noise for the department
        # slots so UC 15 Case A can resolve the inner person name via the
        # LLM verdict map.
        if field_name in DEPT_SLOTS:
            payload, _had_prefix = _strip_co_attn_prefix(stripped)
            payload = _strip_parenthetical_noise(payload)
            # Drop a slash-separated tail so "John Smith/Some Lab" still
            # surfaces "John Smith" for classification.
            if "/" in payload:
                payload = payload.split("/", 1)[0].strip()
            _maybe_add(payload)
            # Also surface the normalised form ("Smith, John" → "John Smith")
            # so a reversed or credentialed person name behind a c/o prefix
            # reaches the classifier too — the same courtesy Name 1 gets.
            cand = _person_candidate(payload)
            if cand:
                _maybe_add(cand)
            continue
        # name1 — legacy behaviour: skip if attn-prefixed.
        if _ATTN_RE.search(stripped):
            continue
        _maybe_add(stripped)
        # Also surface a normalised candidate ("Smith, John" → "John Smith",
        # "John Anderson, PhD" → "John Anderson") so credentialed / reversed
        # person names reach the classifier.
        cand = _person_candidate(stripped)
        if cand:
            _maybe_add(cand)
    return out


PERSON_CLASSIFIER_SYSTEM_PROMPT = (
    "You classify a short text as either a person's name or an "
    "organisation/department/other. Return valid JSON only."
)

PERSON_CLASSIFIER_USER_PROMPT_TEMPLATE = (
    "Text: {text}\n\n"
    "Return JSON:\n"
    "{{\n"
    '  "kind": "person" | "organisation" | "other",\n'
    '  "confidence": "high" | "medium" | "low"\n'
    "}}\n\n"
    "Return 'person' only if you are confident this is a human name. "
    "Anything that could plausibly be a company, department, lab, "
    "research group, or product → 'organisation' or 'other'."
)


async def llm_classify_plain_names_async(llm_client, candidates: list[str]) -> dict[str, str]:
    """Classify each candidate as person / organisation / other.

    Returns a dict mapping ``text.lower()`` → ``"person"`` for
    high-confidence person verdicts only. Low-confidence or org
    verdicts are NOT included (safer to leave the field untouched).
    """
    out: dict[str, str] = {}
    if not candidates:
        return out

    for text in candidates:
        try:
            extraction = await llm_client.extract_json(
                PERSON_CLASSIFIER_SYSTEM_PROMPT,
                PERSON_CLASSIFIER_USER_PROMPT_TEMPLATE.format(text=text),
            )
        except Exception as exc:
            logger.info("Preprocess: plain-name LLM failed for %r: %s", text, exc)
            continue
        kind = (extraction.get("kind") or "").lower()
        conf = (extraction.get("confidence") or "").lower()
        if kind == "person" and conf == "high":
            out[text.lower()] = "person"
    return out
