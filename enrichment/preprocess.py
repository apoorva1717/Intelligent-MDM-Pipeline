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
      "name1": ..., "name2": ..., "name3": ...,
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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

@dataclass
class PreprocessResult:
    name1: str | None = None
    name2: str | None = None
    name3: str | None = None
    care_of: str | None = None
    contact: str | None = None
    email: str | None = None
    street1: str | None = None
    street2: str | None = None
    street3: str | None = None
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

    def note(self, uc: int, reason: str) -> None:
        if uc not in self.use_cases:
            self.use_cases.append(uc)
        self.flags.append(reason)


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
]


def _is_ap_reference(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _AP_PATTERNS)


# ---------------------------------------------------------------------------
# UC 8 — Email copy (non-destructive)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
)


def _find_email(text: str) -> str | None:
    """Return the first email found in *text*, or None.

    The source string is intentionally NOT modified — UC 8 copies the
    email into the dedicated email field but leaves the original
    name/address field untouched (per business rule).
    """
    if not text:
        return None
    m = _EMAIL_RE.search(text)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# UC 9 — Address extraction
# ---------------------------------------------------------------------------

# Street-type suffixes as whole words.
_STREET_SUFFIXES = (
    r"St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|"
    r"Way|Hwy|Highway|Pl|Place|Pkwy|Parkway|Ct|Court|Ter|Terrace|"
    r"Cir|Circle|Sq|Square"
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
        rf"\b\d+\s+(?:{_DIRECTION}\s+)?{_STREET_TOKEN}(?:\s+{_STREET_TOKEN})*\s+(?:{_STREET_SUFFIXES})\b\.?",
        re.IGNORECASE,
    ),
    # Bare street names without house number:
    # "NW 2nd Ave", "S Main St", "E 42nd Street", "North Wolfe Road"
    re.compile(
        rf"^\s*(?:{_DIRECTION}\s+)?{_STREET_TOKEN}(?:\s+{_STREET_TOKEN})*\s+(?:{_STREET_SUFFIXES})\s*$",
        re.IGNORECASE,
    ),
    # "Suite 400", "Ste 400", "Unit 12", "Floor 3", "Bldg 4", "Room 12"
    re.compile(r"\b(?:Suite|Ste|Unit|Floor|Bldg|Building|Room|Rm)\s*\.?\s*[\w\-]+\b", re.IGNORECASE),
    # "PO Box 12345", "P.O. Box 12345", "Post Office Box 12345",
    # bare "Box 100", "Mail Box 5", "Mailbox 42"
    re.compile(
        r"\b(?:P\.?\s*O\.?\s*Box|Post\s+Office\s+Box|Mail\s*Box|Mailbox|Box)\s+\w+\b",
        re.IGNORECASE,
    ),
]


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


def _is_opaque_code(text: str) -> bool:
    if not text:
        return False
    return bool(_OPAQUE_CODE_RE.match(text.strip()))


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

# Anchored to the start so "c/o" inside a phrase doesn't accidentally match.
_CO_ATTN_PREFIX_RE = re.compile(
    r"^\s*(?:c\s*/\s*o|attn(?:ention)?|att)\s*[:\-]?\s*",
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


def _is_job_title(text: str) -> bool:
    return bool(text and _JOB_TITLE_TAIL_RE.search(text))


def _strip_title(name: str) -> str:
    return _TITLE_STRIP_RE.sub("", name).strip()


def _extract_co_attn_from_name2(
    res: PreprocessResult,
    llm_person_verdicts: dict[str, str],
) -> bool:
    """Apply the 5-case c/o + ATTN classifier to Name 2.

    Mutates *res* in place. Returns True if Name 2 was rewritten or
    cleared so the caller can skip the legacy UC 7 Pattern A loop for
    that field.
    """
    val = res.name2
    if not val or not val.strip():
        return False

    payload, had_prefix = _strip_co_attn_prefix(val)
    payload = _strip_parenthetical_noise(payload)
    if not payload:
        # Prefix-only value ("Attn:") — nothing to route, just clear it.
        if had_prefix:
            res.name2 = None
            res.note(15, "name2 cleared — prefix only, no payload")
            return True
        return False

    # ── Case D: Email ────────────────────────────────────────────────
    email_match = _EMAIL_RE.search(payload)
    if email_match:
        email = email_match.group(0)
        payload_is_just_email = re.sub(r"\s+", " ", payload).strip() == email
        if had_prefix or payload_is_just_email:
            if res.email and res.email.strip() and res.email.strip().lower() != email.lower():
                res.note(15, f"Case D: email '{email}' in name2 but Email already populated — flag for review")
                res.flags.append("email-conflict")
            else:
                res.email = email
                res.note(15, f"Case D: email extracted from name2 ({email})")
            res.name2 = None
            return True

    # ── No prefix: only Case E (clear job title) fires ───────────────
    if not had_prefix:
        if _is_job_title(payload):
            res.care_of = payload
            res.name2 = None
            res.note(15, f"Case E: job title routed to care_of ({payload!r})")
            return True
        return False

    # ── Case C: Department / Functional Unit ─────────────────────────
    if _is_department_payload(payload):
        res.name2 = payload
        res.note(15, f"Case C: department/unit kept in name2 (prefix stripped, was {val!r})")
        return True

    # ── Case B: Company ──────────────────────────────────────────────
    if _has_legal_suffix(payload):
        res.care_of = payload
        res.name2 = None
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
            res.note(15, f"Case A: person '{contact_value}' in name2 but Contact already populated — flag for review")
            res.flags.append("contact-conflict")
        else:
            res.contact = contact_value
        res.care_of = care_of_value
        res.name2 = None
        res.trigger_dept_lookup = True
        if flagged_slash:
            res.flags.append(
                f"name2 had slash-separated payload — only leading person "
                f"name retained ({candidate!r}); remainder needs manual review"
            )
        res.note(15, f"Case A: person routed to care_of+contact ({candidate!r})")
        return True

    # ── Case E: Fallback ─────────────────────────────────────────────
    res.care_of = payload
    res.name2 = None
    res.note(15, f"Case E: unclassified payload routed to care_of ({payload!r})")
    return True


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

# A bare plain-name pattern: 2-3 capitalised words.
_PLAIN_NAME_RE = re.compile(
    r"^\s*[A-Z][a-z\-']{1,}\s+(?:[A-Z]\.?\s+)?[A-Z][a-z\-']{1,}(?:\s+[A-Z][a-z\-']{1,})?\s*$",
)


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

    # Pattern B1: title prefix + capitalised name
    if _TITLE_PREFIX_RE.match(stripped):
        return stripped, "", "title-prefix"

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
    llm_person_verdicts: dict[str, str] | None = None,
) -> PreprocessResult:
    """Run all deterministic preprocessing.

    The LLM person classifier (UC 7 Pattern B2) cannot be called
    synchronously from here, so the orchestrator runs an async
    pre-pass over suspicious name fields and passes the verdicts in
    via ``llm_person_verdicts`` (keyed by lowercased text).
    """
    res = PreprocessResult(
        name1=name1, name2=name2, name3=name3,
        contact=contact, email=email,
        street1=street1, street2=street2, street3=street3,
    )
    llm_person_verdicts = llm_person_verdicts or {}

    # ---------------------------------------------------------------
    # UC 15 — c/o + ATTN extraction from Name 2 (5-case classifier).
    # Runs FIRST so the legacy UC 7 Pattern A loop and UC 6 AP
    # normalisation see the already-routed state. Touches Name 2,
    # care_of, contact, email only — Name 1 / Name 3 are untouched.
    # ---------------------------------------------------------------
    name2_handled_by_co_attn = _extract_co_attn_from_name2(res, llm_person_verdicts)

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
    for field_name in ("name1", "name2", "name3"):
        # name2 is handled exclusively by UC 15 above (5-case classifier).
        if field_name == "name2" and name2_handled_by_co_attn:
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
    for field_name in ("name1", "name2", "name3"):
        val = getattr(res, field_name)
        if val and _is_ap_reference(val):
            setattr(res, field_name, "Accounts Payable")
            res.note(6, f"{field_name} normalised to Accounts Payable (was {val!r})")

    # ---------------------------------------------------------------
    # UC 8 — Email copy (non-destructive). Scan name and address
    # fields for an email address. If found, copy it to the email
    # field but DO NOT strip it from the source field.
    # ---------------------------------------------------------------
    for field_name in ("name1", "name2", "name3", "street1", "street2", "street3"):
        val = getattr(res, field_name)
        if not val:
            continue
        email_found = _find_email(val)
        if not email_found:
            continue
        if res.email and res.email.strip():
            if res.email.strip().lower() != email_found.lower():
                res.note(8, f"email present in {field_name} ({email_found}) but Email already populated — flag for review")
                res.flags.append("email-conflict")
        else:
            res.email = email_found
            res.note(8, f"copied email from {field_name} (source field preserved)")

    # ---------------------------------------------------------------
    # UC 9 — Address extraction
    # ---------------------------------------------------------------
    for field_name in ("name1", "name2", "name3"):
        val = getattr(res, field_name)
        if not val:
            continue
        addrs, cleaned = _extract_addresses(val)
        if not addrs:
            continue
        for addr in addrs:
            slot = _first_empty_street_slot(res)
            if slot is None:
                res.note(9, f"address '{addr}' found in {field_name} but all street slots full — flag for review")
                res.flags.append("street-slots-full")
                continue
            setattr(res, slot, addr)
            res.note(9, f"extracted address to {slot} from {field_name}")
        setattr(res, field_name, cleaned or None)

    # ---------------------------------------------------------------
    # UC 11 — DBA normalisation (rewrite any variant to "DBA")
    # ---------------------------------------------------------------
    for field_name in ("name1", "name2", "name3"):
        val = getattr(res, field_name)
        if not val:
            continue
        normalised, changed = _normalise_dba(val)
        if changed:
            setattr(res, field_name, normalised or None)
            res.dba_fields.add(field_name)
            res.note(11, f"{field_name} DBA variant normalised to 'DBA' (was {val!r})")

    # ---------------------------------------------------------------
    # UC 10 — Opaque code / meaningless identifier clearing
    # ---------------------------------------------------------------
    for field_name in ("name2", "name3"):
        val = getattr(res, field_name)
        if val and _is_opaque_code(val):
            res.note(10, f"{field_name} cleared — opaque code {val!r}")
            setattr(res, field_name, None)

    # ---------------------------------------------------------------
    # UC 7 — Contact extraction
    # ---------------------------------------------------------------
    for field_name in ("name1", "name2", "name3"):
        val = getattr(res, field_name)
        if not val:
            continue

        # Pattern A + B1 (deterministic)
        extracted, remaining, reason = _extract_contact_from_field(val)

        # Pattern B2: check LLM verdict if available
        if not extracted and val.strip().lower() in llm_person_verdicts:
            if llm_person_verdicts[val.strip().lower()] == "person":
                extracted = val.strip()
                remaining = ""
                reason = "llm-person"

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

    # ---------------------------------------------------------------
    # UC 14 — Name-slot consolidation.
    # If name2 is empty but name3 has a value, shift name3 → name2.
    # The dept-lookup logic (Tier 1 ROR child match, Tier 2 canonical)
    # operates on name2; a record with the dept in name3 and name2 blank
    # would skip those tiers entirely. Move the value into the slot the
    # pipeline expects.
    # Runs after UC 7/8/9 so a name3 cleared by contact/email/address
    # extraction is not promoted, and before UC 12 so dedup sees the
    # post-shift state.
    # ---------------------------------------------------------------
    if not (res.name2 and res.name2.strip()) and res.name3 and res.name3.strip():
        res.note(14, f"name3 -> name2 shift (name2 was empty, name3={res.name3!r})")
        res.name2 = res.name3.strip()
        res.name3 = None

    # ---------------------------------------------------------------
    # UC 12 — Identical duplicate name fields cleared silently.
    # Runs LAST so that it sees the post-normalisation values
    # (e.g. name1 and name2 both collapsed to "Accounts Payable" by
    # UC 6 will be deduped here). Evaluate name2/name3 before
    # name1/name2 so an all-three-equal case collapses cleanly:
    # ("A","A","A") → name3 cleared first, then name2 cleared.
    # No flag is added — duplicate clearing is informational only.
    # ---------------------------------------------------------------
    def _norm(v: str | None) -> str:
        return re.sub(r"\s+", " ", (v or "").strip()).lower()

    if res.name2 and res.name3 and _norm(res.name2) == _norm(res.name3):
        res.note(12, f"name3 cleared — duplicate of name2 ({res.name3!r})")
        res.name3 = None
    if res.name1 and res.name2 and _norm(res.name1) == _norm(res.name2):
        res.note(12, f"name2 cleared — duplicate of name1 ({res.name2!r})")
        res.name2 = None

    return res


def _first_empty_street_slot(res: PreprocessResult) -> str | None:
    for slot in ("street1", "street2", "street3"):
        if not getattr(res, slot):
            return slot
    return None


# ---------------------------------------------------------------------------
# Async helpers used by the orchestrator
# ---------------------------------------------------------------------------

def find_suspicious_plain_names(
    name1: str | None,
    name2: str | None,
    name3: str | None,
) -> list[str]:
    """Return distinct plain-name candidates that need LLM classification.

    A candidate is a value that:
      - has no known title prefix (Dr., Prof., ...),
      - matches the 2-3 word capitalised pattern,
      - contains NO organisation signal words.

    Name 2 is checked twice: once raw, and once with any leading c/o or
    ATTN prefix stripped (so payloads like "Attn: Victoria Babinetz"
    surface "Victoria Babinetz" as a candidate for UC 15 Case A).
    """
    out: list[str] = []
    seen: set[str] = set()

    def _maybe_add(candidate: str) -> None:
        if not candidate:
            return
        if _TITLE_PREFIX_RE.match(candidate):
            return
        if _ORG_SIGNAL_RE.search(candidate):
            return
        if not _PLAIN_NAME_RE.match(candidate):
            return
        key = candidate.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(candidate)

    for field_name, val in (("name1", name1), ("name2", name2), ("name3", name3)):
        if not val:
            continue
        stripped = val.strip()
        if not stripped:
            continue
        # Strip c/o / ATTN prefix and parenthetical noise for Name 2 so
        # UC 15 Case A can resolve the inner person name via the LLM
        # verdict map.
        if field_name == "name2":
            payload, _had_prefix = _strip_co_attn_prefix(stripped)
            payload = _strip_parenthetical_noise(payload)
            # Drop a slash-separated tail so "John Smith/Some Lab" still
            # surfaces "John Smith" for classification.
            if "/" in payload:
                payload = payload.split("/", 1)[0].strip()
            _maybe_add(payload)
            continue
        # name1 / name3 — legacy behaviour: skip if attn-prefixed.
        if _ATTN_RE.search(stripped):
            continue
        _maybe_add(stripped)
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
