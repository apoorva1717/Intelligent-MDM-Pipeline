"""Entity resolution for a VAT candidate: does the page describe THIS customer?

Deterministic, no LLM. Everything here compares the SAP record with the text of
the page a candidate was read on, as fetched by our own code.

* **Legal form** (:func:`legal_form`): the name's legal form, canonical
  ("Aktiengesellschaft" → ``ag``, "GmbH + Co. KG" → ``gmbh & co kg``), with
  SAP's 35-character cut undone ("… Gm" → ``gmbh``). Stated on both sides and
  different → another legal entity (``LEGAL_FORM_DIFFERS``). Missing in SAP
  only → still the entity (``LEGAL_FORM_MISSING_IN_SAP``), unless the page
  shows a sister entity of the same name (``SISTER_ENTITY_ON_PAGE``).
* **Name** (:func:`name_match`): ``EXACT`` / ``PARTIAL`` / ``MISMATCH`` plus
  the issue codes it raises. Site words (Werk, Niederlassung …) are stripped:
  a branch has no VAT ID of its own (``BRANCH``, not blocking).
* **Address** (:func:`address_match`): ``EXACT`` / ``SAME_TOWN`` /
  ``DIFFERENT_TOWN`` / ``NO_ADDRESS``. A same-name company in the same town
  is rare enough (2.9 % of name + form + town groups in the DE export carry
  different VAT IDs, against 1.9 % for name + form + street) that the town is
  the test, not the street.
* **Domain** and **commercial register number** (:func:`domain_match`,
  :func:`register_match`): optional inputs; supporting evidence only.
* **Weighted score** (:func:`match_score`): reported, decides nothing.
* **Source type and class** (:func:`source_type`, :data:`SOURCE_CLASS`): who
  published the number. A: the entity itself; B: the entity in an official
  channel; C: a third party.
* **Candidate pattern** (:func:`vat_candidates`): ``\\bDE[ ]?[0-9]{9}\\b``
  plus the grouped spelling Impressum pages use ("DE 1477 94 196").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse

from rapidfuzz import fuzz

# ── Name match (VAT_Found_Name_Match) ───────────────────────────────────────
NAME_EXACT = "EXACT"
NAME_PARTIAL = "PARTIAL"
NAME_MISMATCH = "MISMATCH"
#: token_sort_ratio at or above which two names that differ are PARTIAL.
NAME_FUZZY_THRESHOLD = 88.0

# ── Address match (VAT_Found_Address_Match) ─────────────────────────────────
ADDRESS_EXACT = "EXACT"                    # street + house number, postcode, city
ADDRESS_SAME_TOWN = "SAME_TOWN"            # the town matches, the rest need not
ADDRESS_DIFFERENT_TOWN = "DIFFERENT_TOWN"  # the page's address is in another town
ADDRESS_NO_ADDRESS = "NO_ADDRESS"          # the page states no address
ADDRESS_LEVELS = (ADDRESS_EXACT, ADDRESS_SAME_TOWN, ADDRESS_DIFFERENT_TOWN, ADDRESS_NO_ADDRESS)
ACCEPTED_ADDRESS = frozenset({ADDRESS_EXACT, ADDRESS_SAME_TOWN})

# ── Source type (VAT_Found_Source_Type) and class (VAT_Found_Source_Class) ──
SOURCE_COMPANY_IMPRESSUM = "COMPANY_IMPRESSUM"      # the entity's legal notice
SOURCE_COMPANY_WEBSITE = "COMPANY_WEBSITE"          # another page / PDF on its domain
SOURCE_PEPPOL_DIRECTORY = "PEPPOL_DIRECTORY"        # its Peppol participant (9930 = DE VAT)
SOURCE_PROCUREMENT_PROFILE = "PROCUREMENT_PROFILE"  # its buyer profile on a Vergabeportal
SOURCE_OFFICIAL_FILING = "OFFICIAL_FILING"          # Bundesanzeiger / Unternehmensregister
SOURCE_AGGREGATOR = "AGGREGATOR"                    # a directory, or a page nothing ties to it
SOURCE_WEB_ARCHIVE = "WEB_ARCHIVE"                  # an archive snapshot (deleted records only)
CLASS_A, CLASS_B, CLASS_C = "A", "B", "C"
SOURCE_CLASS: dict[str, str] = {
    SOURCE_COMPANY_IMPRESSUM: CLASS_A, SOURCE_COMPANY_WEBSITE: CLASS_A,
    SOURCE_PEPPOL_DIRECTORY: CLASS_A,
    SOURCE_PROCUREMENT_PROFILE: CLASS_B, SOURCE_OFFICIAL_FILING: CLASS_B,
    SOURCE_AGGREGATOR: CLASS_C, SOURCE_WEB_ARCHIVE: CLASS_C,
}
TRUSTED_CLASSES = frozenset({CLASS_A, CLASS_B})

#: Registrable domains per kind of host.
OFFICIAL_FILING_DOMAINS: frozenset[str] = frozenset({"bundesanzeiger.de", "unternehmensregister.de"})
#: Entity confirmation only (legal name, HRB): they publish no VAT IDs.
REGISTER_DOMAINS: frozenset[str] = frozenset({"handelsregister.de", "e-justice.europa.eu"})
PROCUREMENT_DOMAINS: frozenset[str] = frozenset({
    "bund.de", "dtvp.de", "evergabe.de", "evergabe-online.de", "subreport.de",
    "vergabe.nrw.de", "vergabemarktplatz.de", "deutsche-evergabe.de", "staatsanzeiger-eservices.de",
})
PEPPOL_DOMAINS: frozenset[str] = frozenset({"peppol.eu"})
ARCHIVE_DOMAINS: frozenset[str] = frozenset({"archive.org", "archive.today", "archive.ph"})
#: Aggregator hosts, matched against the host's labels (``northdata`` covers
#: northdata.de and northdata.com; entries of six characters or more also
#: match inside a label, so ``branchenbuch`` covers branchenbuch-deutschland).
AGGREGATOR_LABELS: frozenset[str] = frozenset({
    "northdata", "firmenwissen", "wlw", "dnb", "companyhouse", "implisense",
    "creditreform", "kompass", "wer-zu-wem", "werzuwem", "yellowpages", "gelbeseiten",
    "dasoertliche", "11880", "cylex", "firmenabc", "meinestadt", "werliefertwas",
    "firmeneintrag", "branchenbuch", "unternehmensverzeichnis",
})
#: Marketplaces and social networks: never a source; a candidate there is dropped.
REJECTED_LABELS: frozenset[str] = frozenset({
    "amazon", "ebay", "etsy", "alibaba", "facebook", "linkedin", "xing", "instagram",
    "twitter", "youtube", "tiktok", "pinterest", "kununu", "indeed", "stepstone",
})

# ── Issue codes (VAT_Found_Issues), all of them ─────────────────────────────
ADDRESS_DIFFERS = "ADDRESS_DIFFERS"            # SAME_TOWN
ADDRESS_MISMATCH = "ADDRESS_MISMATCH"          # DIFFERENT_TOWN
ADDRESS_NOT_ON_PAGE = "ADDRESS_NOT_ON_PAGE"    # NO_ADDRESS
LEGAL_FORM_DIFFERS = "LEGAL_FORM_DIFFERS"      # prefix: ``LEGAL_FORM_DIFFERS:<sap>/<page>``
LEGAL_FORM_MISSING_IN_SAP = "LEGAL_FORM_MISSING_IN_SAP"
BRANCH = "BRANCH"
ISSUE_NAME_PARTIAL = "NAME_PARTIAL"
SISTER_ENTITY_ON_PAGE = "SISTER_ENTITY_ON_PAGE"
DEPARTMENT = "DEPARTMENT"
SECONDARY_SOURCE = "SECONDARY_SOURCE"
OTHER_VAT_IDS_ON_PAGE = "OTHER_VAT_IDS_ON_PAGE"
VIES_INVALID = "VIES_INVALID"
VIES_UNAVAILABLE = "VIES_UNAVAILABLE"
#: The number is queued for VIES (vat/vies_retry.py) and has no answer yet.
#: Not blocking; nothing is written to SAP until VIES says VALID.
VIES_PENDING = "VIES_PENDING"
CONFLICTING_SOURCES = "CONFLICTING_SOURCES"
#: The page states a register entry of the same kind, and it is not ours.
REGISTER_MISMATCH = "REGISTER_MISMATCH"
#: The number could not be checked on a page we read ourselves: the quote or
#: the digits are not on it, the number is malformed, the page could not be
#: fetched, or there is no page at all.
EVIDENCE_NOT_VERIFIED = "EVIDENCE_NOT_VERIFIED"
SAP_VAT_OUTDATED = "SAP_VAT_OUTDATED"
#: Name 2–4 names its own legal entity under the Name 1 umbrella; the number
#: is that entity's (vat/name_prep.py).
SUB_ENTITY = "SUB_ENTITY"
#: The reader named no number; the one number on a page that names the
#: entity was taken by pattern.
EXTRACTED_BY_PATTERN = "EXTRACTED_BY_PATTERN"
#: The number is an umbrella parent's (vat/umbrella_entities.yaml) that the
#: page says the record belongs to (Fraunhofer, Max-Planck): AUTO allowed.
PARENT_ENTITY = "PARENT_ENTITY"
#: The page names the entity but gives another organisation's number (a
#: parent outside the umbrella list): review, never AUTO.
PARENT_ENTITY_CANDIDATE = "PARENT_ENTITY_CANDIDATE"
#: A GENERIC name or an ABBREVIATED match needs the address EXACT for AUTO.
ADDRESS_NOT_EXACT = "ADDRESS_NOT_EXACT"
#: A directory entry under another name lists the record's as a former
#: name (LX International for LG International): review, never AUTO.
SUCCESSOR_CANDIDATE = "SUCCESSOR_CANDIDATE"
ISSUE_ORDER: tuple[str, ...] = (
    ADDRESS_DIFFERS, ADDRESS_MISMATCH, ADDRESS_NOT_ON_PAGE, ADDRESS_NOT_EXACT,
    LEGAL_FORM_DIFFERS, LEGAL_FORM_MISSING_IN_SAP, BRANCH, ISSUE_NAME_PARTIAL, SUCCESSOR_CANDIDATE,
    SISTER_ENTITY_ON_PAGE, DEPARTMENT, SUB_ENTITY, PARENT_ENTITY, PARENT_ENTITY_CANDIDATE,
    EXTRACTED_BY_PATTERN,
    SECONDARY_SOURCE, OTHER_VAT_IDS_ON_PAGE, VIES_INVALID, VIES_UNAVAILABLE, VIES_PENDING,
    CONFLICTING_SOURCES, REGISTER_MISMATCH, EVIDENCE_NOT_VERIFIED,
    SAP_VAT_OUTDATED,
)
#: Any of these keeps a row out of AUTO_ACCEPT.
BLOCKING_ISSUES = frozenset({
    LEGAL_FORM_DIFFERS, ISSUE_NAME_PARTIAL, SISTER_ENTITY_ON_PAGE, SECONDARY_SOURCE,
    VIES_INVALID, CONFLICTING_SOURCES, ADDRESS_MISMATCH, ADDRESS_NOT_ON_PAGE,
    REGISTER_MISMATCH, EVIDENCE_NOT_VERIFIED, PARENT_ENTITY_CANDIDATE, ADDRESS_NOT_EXACT,
    SUCCESSOR_CANDIDATE,
})
ADDRESS_ISSUE = {
    ADDRESS_SAME_TOWN: ADDRESS_DIFFERS, ADDRESS_DIFFERENT_TOWN: ADDRESS_MISMATCH,
    ADDRESS_NO_ADDRESS: ADDRESS_NOT_ON_PAGE,
}


def issue_code(issue: str) -> str:
    """``LEGAL_FORM_DIFFERS:gmbh/ag`` → ``LEGAL_FORM_DIFFERS``."""
    return issue.split(":", 1)[0]


def is_blocking(issue: str) -> bool:
    return issue_code(issue) in BLOCKING_ISSUES


def sort_issues(issues) -> tuple[str, ...]:
    """Unique, in :data:`ISSUE_ORDER`."""
    rank = {code: i for i, code in enumerate(ISSUE_ORDER)}
    unique = list(dict.fromkeys(issues))
    return tuple(sorted(unique, key=lambda i: rank.get(issue_code(i), len(rank))))


# ── Flags (VAT_Found_Flags) ─────────────────────────────────────────────────
DOMAIN_MATCH = "DOMAIN_MATCH"
DOMAIN_MISMATCH = "DOMAIN_MISMATCH"
REGISTER_MATCH = "REGISTER_MATCH"
#: Prefix: mandatory inputs the record lacks (``…:street,postal_code``).
INPUT_INCOMPLETE = "INPUT_INCOMPLETE"

#: Indicative weights, in percent.
WEIGHTS: dict[str, int] = {
    "name": 30, "street": 25, "postal_code": 15, "city": 10, "domain": 10, "register": 10,
}
_NAME_CREDIT = {NAME_EXACT: 1.0, NAME_PARTIAL: 0.5, NAME_MISMATCH: 0.0}


def fold(text: str | None, *, umlaut_e: bool = True) -> str:
    """Lower case, umlauts folded (``ü`` → ``ue``, or → ``u`` with
    *umlaut_e* False), ``ß`` → ``ss``, other accents dropped, everything that
    is not a letter or digit a single space."""
    t = (text or "").lower().replace("ß", "ss")
    if umlaut_e:
        t = t.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", t).split())


# ---------------------------------------------------------------------------
# Legal form
# ---------------------------------------------------------------------------

#: Long forms → canonical token, applied to folded text in this order.
_LONG_FORMS: tuple[tuple[str, str], ...] = (
    ("gesellschaft mit beschraenkter haftung", "gmbh"),
    ("kommanditgesellschaft auf aktien", "kgaa"),
    ("offene handelsgesellschaft", "ohg"),
    ("eingetragene genossenschaft", "eg"),
    ("eingetragener verein", "ev"),
    ("koerperschaft des oeffentlichen rechts", "kdoer"),
    ("anstalt des oeffentlichen rechts", "adoer"),
    ("societas europaea", "se"),
    ("unternehmergesellschaft", "ug"),
    ("kommanditgesellschaft", "kg"),
    ("aktiengesellschaft", "ag"),
    ("mbh", "gmbh"),
    ("gemeinnuetzige gmbh", "ggmbh"),
    ("limited", "ltd"),
    ("incorporated", "inc"),
)
#: Canonical legal-form tokens.
_FORMS = frozenset({
    "gmbh", "ggmbh", "ag", "kgaa", "kg", "ohg", "se", "ug", "ev", "eg", "kdoer", "adoer",
    "gbr", "ltd", "inc", "llc", "plc", "bv", "nv", "sa", "sarl", "sas", "spa", "srl",
})
#: "& Co." between two forms; never part of the name.
_CONNECTORS = frozenset({"co", "cie"})
#: SAP cuts Name 1 at this many characters.
SAP_NAME_LIMIT = 35


def _form_tokens(name: str) -> list[str]:
    """Folded tokens with dotted single letters re-joined (``e. V.`` → ``ev``,
    ``G.m.b.H.`` → ``gmbh``) and long forms replaced by their abbreviation."""
    text = f" {fold(name)} "
    joined: list[str] = []
    run = ""
    for word in [*text.split(), ""]:
        if len(word) == 1 and word.isalpha():
            run += word
            continue
        if run:
            joined.append(run)
            run = ""
        if word:
            joined.append(word)
    text = f" {' '.join(joined)} "
    for long, short in _LONG_FORMS:
        text = text.replace(f" {long} ", f" {short} ")
    return text.split()


def _canonical(forms: list[str]) -> str | None:
    """``["gmbh", "kg"]`` → ``"gmbh & co kg"``; one form as it is."""
    forms = list(dict.fromkeys(forms))
    if not forms:
        return None
    return forms[0] if len(forms) == 1 else f"{forms[0]} & co {forms[-1]}"


def _truncated(tokens: list[str]) -> tuple[list[str], str | None, bool]:
    """A legal form cut off at SAP's 35 characters, read back from the
    trailing fragment: ``(tokens without it, form, recognised)``. "… Gm" /
    "… Gmb" → gmbh; "… GmbH & Co" / "… GmbH & C" → gmbh & co kg; a prefix of
    a spelled-out form ("… Aktiengesel") → that form; "… A" → ambiguous: no
    form, fragment dropped."""
    if not tokens:
        return tokens, None, False
    last = tokens[-1]
    if last in ("gm", "gmb"):
        return tokens[:-1], "gmbh", True
    if len(tokens) >= 2 and tokens[-2] in _FORMS and last in ("c", "co", "cok", "k"):
        return tokens[:-1], _canonical([tokens[-2], "kg"]), True
    if len(tokens) >= 3 and tokens[-3] in _FORMS and tokens[-2] == "co" and last == "k":
        return tokens[:-2], _canonical([tokens[-3], "kg"]), True
    for k in range(min(4, len(tokens)), 0, -1):
        tail = " ".join(tokens[-k:])
        if len(tail) < 5:
            continue
        for long, short in _LONG_FORMS:
            if len(long) > len(tail) and long.startswith(tail):
                return tokens[:-k], short, True
    if len(last) == 1:
        return tokens[:-1], None, True
    return tokens, None, False


@dataclass(frozen=True)
class EntityName:
    """A name split into its comparison core and its legal form."""

    core: tuple[str, ...]
    form: str | None


def parse_name(name: str | None, *, sap: bool = False) -> EntityName:
    """Core tokens (legal forms and "& Co." removed) and the canonical legal
    form. *sap*: the name may have been cut at :data:`SAP_NAME_LIMIT`."""
    tokens = _form_tokens(name or "")
    cut_form = None
    if sap and len((name or "").strip()) >= SAP_NAME_LIMIT - 2:
        tokens, cut_form, _ = _truncated(tokens)
    forms = [t for t in tokens if t in _FORMS]
    if cut_form:
        forms += cut_form.split(" & co ")
    core = tuple(t for t in tokens if t not in _FORMS and t not in _CONNECTORS)
    return EntityName(core or tuple(tokens), _canonical(forms))


def legal_form(name: str | None, *, sap: bool = False) -> str | None:
    """The canonical legal form of *name* (``"ag & co kgaa"`` for "Henkel AG
    & Co. KGaA"); None when it states none."""
    return parse_name(name, sap=sap).form


def legal_forms_differ(page_name: str | None, record_name: str | None) -> tuple[str, str] | None:
    """``(record form, page form)`` when both state a legal form and they
    differ; None otherwise."""
    page, record = legal_form(page_name), legal_form(record_name, sap=True)
    if page and record and page != record:
        return record, page
    return None


# ---------------------------------------------------------------------------
# Name
# ---------------------------------------------------------------------------

#: A site of the entity: stripped with everything after it. A branch of a
#: German company has no VAT ID of its own, so the entity's number is its.
SITE_WORDS: tuple[str, ...] = (
    "werk", "niederlassung", "zweigniederlassung", "standort", "betriebsstaette", "filiale",
)
#: A department of the entity (usually in Name 2–4): the entity is Name 1.
DEPARTMENT_WORDS: tuple[tuple[str, ...], ...] = tuple(tuple(w.split()) for w in (
    "institut", "lehrstuhl", "fakultaet", "fachbereich", "klinik fuer", "abteilung",
    "zentrum", "labor", "professur", "arbeitsgruppe",
))


def _starts_with(tokens: tuple[str, ...], i: int, words: tuple[str, ...]) -> bool:
    return tuple(tokens[i:i + len(words)]) == words


def _split_at(tokens: tuple[str, ...], phrases) -> tuple[str, ...] | None:
    """The tokens before the first phrase that starts after the first token."""
    for i in range(1, len(tokens)):
        if any(_starts_with(tokens, i, p) for p in phrases):
            return tokens[:i]
    return None


def has_department(text: str | None) -> bool:
    tokens = tuple(fold(text).split())
    return any(_starts_with(tokens, i, p) for i in range(len(tokens)) for p in DEPARTMENT_WORDS)


@dataclass(frozen=True)
class NameVerdict:
    match: str                     # NAME_EXACT / NAME_PARTIAL / NAME_MISMATCH
    issues: tuple[str, ...] = ()
    core: tuple[str, ...] = ()     # the record's entity name, as compared
    record_form: str | None = None
    page_form: str | None = None


_MATCH_RANK = {NAME_EXACT: 0, NAME_PARTIAL: 1, NAME_MISMATCH: 2}


def _compare(page: EntityName, record: EntityName, extra: tuple[str, ...]) -> NameVerdict:
    issues = list(extra)
    if set(page.core) == set(record.core):
        match = NAME_EXACT
    elif set(page.core) & set(record.core) or fuzz.token_sort_ratio(
            " ".join(page.core), " ".join(record.core)) >= NAME_FUZZY_THRESHOLD:
        match, issues = NAME_PARTIAL, [*issues, ISSUE_NAME_PARTIAL]
    else:
        match, issues = NAME_MISMATCH, [*issues, ISSUE_NAME_PARTIAL]
    if page.form and record.form and page.form != record.form:
        match = NAME_PARTIAL if match == NAME_EXACT else match
        issues.append(f"{LEGAL_FORM_DIFFERS}:{record.form}/{page.form}")
    elif page.form and not record.form and match == NAME_EXACT:
        issues.append(LEGAL_FORM_MISSING_IN_SAP)
    return NameVerdict(match, sort_issues(issues), record.core, record.form, page.form)


def _variants(name: EntityName, *, departments: bool) -> list[tuple[EntityName, tuple[str, ...]]]:
    """The name as given, without a site phrase (BRANCH) and, for the
    record, without a department phrase (DEPARTMENT)."""
    out = [(name, ())]
    site = _split_at(name.core, [(w,) for w in SITE_WORDS])
    if site:
        out.append((EntityName(site, name.form), (BRANCH,)))
    dept = _split_at(name.core, DEPARTMENT_WORDS) if departments else None
    if dept:
        out.append((EntityName(dept, name.form), (DEPARTMENT,)))
    return out


def name_match(page_name: str | None, names: tuple[str | None, ...]) -> NameVerdict:
    """The page's entity name against the record: Name 1 alone, and Name 1–4
    joined unless Name 2–4 hold a department (the legal entity is then Name
    1). The best verdict wins; on a tie, the one with fewer issues."""
    page = parse_name(page_name)
    if not page_name or not page.core:
        return NameVerdict(NAME_MISMATCH, (ISSUE_NAME_PARTIAL,))
    parts = [str(n).strip() for n in names if n and str(n).strip()]
    if not parts:
        return NameVerdict(NAME_MISMATCH, (ISSUE_NAME_PARTIAL,))
    records = [parse_name(parts[0], sap=True)]
    extra = parts[1:]
    if extra and not any(has_department(p) for p in extra):
        records.append(parse_name(" ".join(parts), sap=True))
    verdicts = [
        _compare(p, r, p_extra + r_extra)
        for record in records
        for r, r_extra in _variants(record, departments=True)
        for p, p_extra in _variants(page, departments=False)
    ]
    return min(verdicts, key=lambda v: (_MATCH_RANK[v.match], len(v.issues)))


def forms_on_page(page_text: str | None, core: tuple[str, ...]) -> set[str]:
    """Every legal form the page writes directly after the name *core*."""
    tokens = _form_tokens(page_text or "")
    n, found = len(core), set()
    if not n:
        return found
    for i in range(len(tokens) - n + 1):
        if tuple(tokens[i:i + n]) != core:
            continue
        forms, j = [], i + n
        while j < len(tokens) and j < i + n + 4 and (tokens[j] in _FORMS or tokens[j] in _CONNECTORS):
            if tokens[j] in _FORMS:
                forms.append(tokens[j])
            j += 1
        if forms:
            found.add(_canonical(forms))
    return found


def entity_name_on_page(page_text: str | None, names: tuple[str | None, ...]) -> str | None:
    """The record's entity as the page names it, for a page read without a
    model: Name 1's core (as given, or without a site / department phrase)
    found on the page, with the legal form the page writes after it (the
    record's own when the page writes several). When the record states a
    legal form, the page must write the name WITH a legal form: a bare
    brand mention ("k-labor" on DEKRA's Impressum) does not say whose legal
    notice it is. Folded text; None when the page does not name the entity."""
    if not names or not names[0]:
        return None
    record = parse_name(names[0], sap=True)
    tokens = _form_tokens(page_text or "")
    for variant, _ in _variants(record, departments=True):
        core, n = variant.core, len(variant.core)
        if not n or not any(tuple(tokens[i:i + n]) == core for i in range(len(tokens) - n + 1)):
            continue
        forms = forms_on_page(page_text, core)
        if record.form and not forms:
            continue
        form = record.form if record.form in forms else (sorted(forms)[0] if forms else None)
        return " ".join(core) + (f" {form}" if form else "")
    return None


_GENERAL_PARTNER_RE = re.compile(
    r"komplementaer|persoenlich haftende[rn]? gesellschafter", re.IGNORECASE)


def sister_entity_on_page(page_text: str | None, verdict: NameVerdict) -> bool:
    """With the form missing in SAP: the page shows a second entity of the
    same name with another legal form ("Müller GmbH" and "Müller GmbH & Co.
    KG"), or names the GmbH as the general partner (Komplementär) of a KG.
    Either way SAP's record could be either entity."""
    if LEGAL_FORM_MISSING_IN_SAP not in verdict.issues:
        return False
    forms = forms_on_page(page_text, verdict.core)
    if forms - {verdict.page_form}:
        return True
    partner = bool(_GENERAL_PARTNER_RE.search(fold(page_text)))
    return partner and verdict.page_form in ("gmbh", "ag", "se", "ug") and "kg" in _form_tokens(
        page_text or "")


# ---------------------------------------------------------------------------
# Address
# ---------------------------------------------------------------------------

#: A German address block: a 5-digit postcode followed by a capitalised word.
ADDRESS_BLOCK_RE = re.compile(r"(?<![\d-])(?:D-?\s?)?(\d{5})\s+(?=[A-ZÄÖÜ])([^\n]{0,40})")
_STREET_SUFFIX_RE = re.compile(r"\b([a-z]+?)\s?(?:strasse|str)\b")
_POSTFACH_RE = re.compile(r"\bpostfach\b|\bp\s?o\s?box\b", re.IGNORECASE)


def _street_norm(text: str | None) -> str:
    """Folded, with the street suffix abbreviation undone and joined to its
    word: "Hauptstr. 5", "Haupt-Straße 5" and "Hauptstrasse 5" all become
    ``hauptstrasse 5``."""
    return _STREET_SUFFIX_RE.sub(r"\1strasse", fold(text))


def street_on_page(page_text: str, street: str | None, house_number: str | None) -> bool | None:
    """Street and house number, as whole words, on the page; None when the
    record has no street. "Platz 1" does not match "Platz 12"."""
    if not street:
        return None
    wanted = _street_norm(" ".join(p for p in (street, house_number) if p))
    if not wanted:
        return None
    return f" {wanted} " in f" {_street_norm(page_text)} "


def postal_code_on_page(page_text: str, postal_code: str | None) -> bool | None:
    plz = re.sub(r"\D", "", postal_code or "")
    if len(plz) != 5:
        return None
    return bool(re.search(rf"(?<!\d){plz}(?!\d)", page_text or ""))


def city_on_page(page_text: str, city: str | None) -> bool | None:
    """The City on the page: in a ``<postcode> <town>`` block when the page
    has one (so "Amtsgericht Stuttgart" is not the town), anywhere otherwise
    ("Postfach 30 02 20, Gerlingen"). Umlaut-folded either way; the City's
    longest token is enough ("FRANKFURT AM MAIN" vs "60549 Frankfurt")."""
    if not fold(city):
        return None
    blocks = [m.group(2) for m in ADDRESS_BLOCK_RE.finditer(page_text or "")]
    for umlaut_e in (True, False):
        sap_city = fold(city, umlaut_e=umlaut_e)
        main = max(sap_city.split(), key=len)
        if blocks:
            for b in blocks:
                town = fold(b, umlaut_e=umlaut_e)
                if f" {sap_city} " in f" {town} " or (len(main) >= 4 and main in town.split()):
                    return True
            continue
        if f" {sap_city} " in f" {fold(page_text, umlaut_e=umlaut_e)} ":
            return True
    return False


def has_address(page_text: str | None) -> bool:
    return bool(ADDRESS_BLOCK_RE.search(page_text or "") or _POSTFACH_RE.search(page_text or ""))


def town_match(page_text: str, postal_code: str | None, city: str | None) -> bool | None:
    """The page's town is the record's: its City, or its postcode (a German
    postcode lies in one town). None when the record has neither."""
    hits = [postal_code_on_page(page_text, postal_code), city_on_page(page_text, city)]
    if all(h is None for h in hits):
        return None
    return any(hits)


def address_match(
    page_text: str, street: str | None, house_number: str | None,
    postal_code: str | None, city: str | None,
) -> tuple[str, dict[str, bool | None]]:
    """The address level and the per-element results (None: no input)."""
    parts = {
        "street": street_on_page(page_text, street, house_number),
        "postal_code": postal_code_on_page(page_text, postal_code),
        "city": city_on_page(page_text, city),
    }
    if all(parts.values()):
        level = ADDRESS_EXACT
    elif parts["postal_code"] or parts["city"]:
        level = ADDRESS_SAME_TOWN
    elif has_address(page_text):
        level = ADDRESS_DIFFERENT_TOWN
    else:
        level = ADDRESS_NO_ADDRESS
    return level, parts


def address_text(page_text: str | None, window: int = 160) -> str | None:
    """The parts of a page an address match reads: the text around every
    postcode block and PO box. Kept on the result so each SAP record of the
    entity can be matched against the same page later."""
    text = page_text or ""
    if len(text) <= 4 * window:
        return text or None
    spans = [(max(0, m.start() - window), m.end() + 40)
             for rx in (ADDRESS_BLOCK_RE, _POSTFACH_RE) for m in rx.finditer(text)]
    if not spans:
        return None
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return " … ".join(text[s:e] for s, e in merged[:20])


# ---------------------------------------------------------------------------
# Domain and register number
# ---------------------------------------------------------------------------

#: Mailbox providers: an email address there says nothing about the company.
_MAIL_PROVIDERS = frozenset({
    "gmail.com", "googlemail.com", "web.de", "gmx.de", "gmx.net", "t-online.de",
    "outlook.com", "hotmail.com", "yahoo.com", "yahoo.de", "icloud.com", "aol.com",
    "freenet.de", "posteo.de", "mail.de",
})


def registrable_domain(host_or_url: str | None) -> str | None:
    """``www.bosch.de`` / ``https://shop.bosch.de/x`` / ``a@bosch.de`` → ``bosch.de``."""
    text = (host_or_url or "").strip().lower()
    if not text:
        return None
    if "@" in text and "/" not in text:
        text = text.rsplit("@", 1)[1]
    host = urlparse(text if "//" in text else f"//{text}").hostname or ""
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return None
    # co.uk, com.au …: keep three labels.
    keep = 3 if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "ac", "gov") else 2
    return ".".join(labels[-keep:])


def company_domain(website: str | None, email: str | None) -> str | None:
    """The customer's own domain: the website, else a non-mailbox email's."""
    if website and registrable_domain(website):
        return registrable_domain(website)
    domain = registrable_domain(email) if email and "@" in email else None
    return domain if domain and domain not in _MAIL_PROVIDERS else None


def domain_match(page_url: str, domain: str | None) -> bool | None:
    if not domain:
        return None
    return registrable_domain(page_url) == domain


#: Name words that say nothing about whose domain it is.
_GENERIC_WORDS = frozenset({
    "deutschland", "germany", "gruppe", "group", "holding", "service", "services",
    "international", "und", "der", "die", "das", "des", "fuer", "von", "zu", "am",
    "an", "im", "in", "the", "and", "of",
})


def domain_resembles(url: str, core: tuple[str, ...]) -> bool:
    """The page's domain is named after the entity: a distinctive name word
    is in it (``uni-stuttgart.de`` for "Universität Stuttgart"), or the
    name's initials are a domain label (``kit.edu``)."""
    domain = registrable_domain(url) or ""
    label = domain.rsplit(".", 1)[0]
    if not label:
        return False
    flat = re.sub(r"[^a-z0-9]", "", label)
    words = [w for w in core if w not in _GENERIC_WORDS]
    if any(len(w) >= 4 and w in flat for w in words):
        return True
    initials = "".join(w[0] for w in words if w)
    return len(initials) >= 2 and initials in re.split(r"[^a-z0-9]+", label)


_REGISTER_RE = re.compile(r"\b(HRA|HRB|GnR|PR|VR|GsR)\s*[.:]?\s*(\d{1,7})\s*([A-Z]{0,2})\b", re.IGNORECASE)


def register_key(text: str | None) -> tuple[str, str] | None:
    """``"Amtsgericht Stuttgart HRB 14000"`` → ``("HRB", "14000")``."""
    m = _REGISTER_RE.search(text or "")
    if not m:
        return None
    return m.group(1).upper(), m.group(2).lstrip("0") or "0"


def register_match(page_text: str, register_number: str | None) -> bool | None:
    """True: the page states our register entry. False: it states another
    entry of the same kind and not ours. None: no input, or the page states
    none of that kind (not evidence either way)."""
    ours = register_key(register_number)
    if ours is None:
        return None
    seen = {(m.group(1).upper(), m.group(2).lstrip("0") or "0")
            for m in _REGISTER_RE.finditer(page_text or "")}
    if ours in seen:
        return True
    return False if any(kind == ours[0] for kind, _ in seen) else None


# ---------------------------------------------------------------------------
# Score and source
# ---------------------------------------------------------------------------

def match_score(name: str, elements: dict[str, bool | None]) -> int:
    """The weighted score, 0–100, over the elements the record carries (an
    element with no input is left out of the denominator)."""
    got = WEIGHTS["name"] * _NAME_CREDIT.get(name, 0.0)
    total = WEIGHTS["name"]
    for key, value in elements.items():
        if value is None:
            continue
        total += WEIGHTS[key]
        got += WEIGHTS[key] if value else 0
    return round(100 * got / total)


def _host(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower()


def _on(url: str, domains: frozenset[str]) -> bool:
    host = _host(url)
    return any(host == d or host.endswith("." + d) for d in domains)


def _has_label(url: str, labels: frozenset[str]) -> bool:
    for label in _host(url).split("."):
        for entry in labels:
            if label == entry or (len(entry) >= 6 and entry in label):
                return True
    return False


def is_register_host(url: str) -> bool:
    return _on(url, REGISTER_DOMAINS | OFFICIAL_FILING_DOMAINS)


def is_aggregator_host(url: str) -> bool:
    return _has_label(url, AGGREGATOR_LABELS)


def is_rejected_host(url: str) -> bool:
    """A marketplace or social network: never a source."""
    domain = registrable_domain(url) or ""
    return domain.split(".")[0] in REJECTED_LABELS or domain in ("x.com", "t.co")


def is_archive_host(url: str) -> bool:
    return _on(url, ARCHIVE_DOMAINS)


def is_procurement_host(url: str) -> bool:
    return _on(url, PROCUREMENT_DOMAINS) or "vergabe" in _host(url)


def is_third_party_host(url: str) -> bool:
    """Not the entity's own website, whatever the page says."""
    return (is_aggregator_host(url) or is_register_host(url) or is_archive_host(url)
            or is_procurement_host(url) or _on(url, PEPPOL_DOMAINS) or is_rejected_host(url))


_ARCHIVE_DATE_RE = re.compile(r"/web/(\d{4})(\d{2})(\d{2})\d*")


def archive_snapshot_date(url: str) -> str | None:
    """``web.archive.org/web/20210305123456/…`` → ``2021-03-05``."""
    m = _ARCHIVE_DATE_RE.search(urlparse(url or "").path)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def source_type(
    url: str, *, impressum: bool, own_domain: bool | None, core: tuple[str, ...] = (),
) -> str:
    """Who published the number on the page at *url*. *own_domain*: the page
    is on the customer's website / email domain (None: no such input);
    *core*: the entity's name, for :func:`domain_resembles`."""
    if is_archive_host(url):
        return SOURCE_WEB_ARCHIVE
    if _on(url, PEPPOL_DOMAINS):
        return SOURCE_PEPPOL_DIRECTORY
    if _on(url, OFFICIAL_FILING_DOMAINS):
        return SOURCE_OFFICIAL_FILING
    if is_procurement_host(url):
        return SOURCE_PROCUREMENT_PROFILE
    if is_aggregator_host(url) or is_register_host(url) or is_rejected_host(url):
        return SOURCE_AGGREGATOR
    if impressum:
        return SOURCE_COMPANY_IMPRESSUM
    if own_domain or domain_resembles(url, core):
        return SOURCE_COMPANY_WEBSITE
    return SOURCE_AGGREGATOR


# ---------------------------------------------------------------------------
# Candidate pattern
# ---------------------------------------------------------------------------

#: The design paper's pattern, verbatim.
VAT_PATTERN = re.compile(r"\bDE[ ]?[0-9]{9}\b")
#: The same with the digit grouping legal notices use ("DE 1477 94 196",
#: "DE 811.128.135"). A trailing ``-NNNNN`` is a Wirtschafts-Identifikations-
#: nummer (W-IdNr.), not a VAT ID.
_GROUPED_PATTERN = re.compile(r"\bDE[ ]?((?:\d[ .]?){8}\d)\b(?!-\d{5})")


def vat_candidates(text: str | None) -> list[str]:
    """Every German VAT-ID-shaped string on a page, normalised (spaces and
    dots removed, upper case), in page order, without repeats. Candidates
    only: a match says nothing about whose number it is, or whether it is
    valid."""
    found: list[str] = []
    for m in _GROUPED_PATTERN.finditer(text or ""):
        value = "DE" + re.sub(r"\D", "", m.group(1))
        if value not in found:
            found.append(value)
    return found
