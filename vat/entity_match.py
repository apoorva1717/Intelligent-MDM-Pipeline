"""Entity resolution for a VAT candidate: does the page describe THIS customer?

Implements §5–6 of the design paper ("German VAT ID Research Agent", 2026-10-27).
Deterministic, no LLM. Everything here compares the SAP record with the text of
the page a candidate was read on, as fetched by our own code.

* **Legal form** (:func:`legal_form`) — the name's legal form(s), canonical
  ("Aktiengesellschaft" → ``ag``, "GmbH & Co. KG" → ``gmbh kg``). A page whose
  legal form differs from the record's is a different legal entity, however
  alike the names are (§5: "legal form and distinctive terms must be
  preserved"). Only a difference counts: a side that states no form is not
  evidence either way.
* **Address** (:func:`address_match`) — street + house number, postal code
  and city, each checked on the page, rolled up to ``EXACT`` / ``STRONG`` /
  ``PARTIAL`` / ``MISMATCH`` / ``UNKNOWN``.
* **Domain** and **commercial register number** (:func:`domain_match`,
  :func:`register_match`) — optional inputs; supporting evidence only.
* **Weighted score** (:func:`match_score`) — the paper's indicative weights.
  It *supports* the decision and is reported; it never replaces a rule
  (``vat/lookup.py`` :func:`~vat.lookup.decide`).
* **Source type** (:func:`source_type`) — the paper's source hierarchy (§4).
* **Candidate pattern** (:func:`vat_candidates`) — the paper's
  ``\\bDE[ ]?[0-9]{9}\\b``, plus the grouped spelling Impressum pages use
  ("DE 1477 94 196"). A match is a candidate, never a verification.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

# ── Name match (VAT_Found_Name_Match) ───────────────────────────────────────
NAME_EXACT = "EXACT"
#: The record is a site / department of the page's entity (Werk, Standort …).
NAME_BRANCH = "BRANCH"
NAME_PARTIAL = "PARTIAL"
NAME_MISMATCH = "MISMATCH"

# ── Address match (VAT_Found_Address_Match) ─────────────────────────────────
ADDRESS_EXACT = "EXACT"        # street + house number, postal code and city
ADDRESS_STRONG = "STRONG"      # postal code and one of street / city
ADDRESS_PARTIAL = "PARTIAL"    # one of the three
ADDRESS_NONE = "MISMATCH"      # the page states an address, none of ours
ADDRESS_NOT_STATED = "UNKNOWN"  # the page states no address we can see
STRONG_ADDRESS = frozenset({ADDRESS_EXACT, ADDRESS_STRONG})

# ── Source type (VAT_Found_Source_Type), §4 ─────────────────────────────────
#: Tier 1: a legal notice (Impressum / Imprint / Legal Notice).
SOURCE_COMPANY_IMPRESSUM = "COMPANY_IMPRESSUM"
#: Tier 1: another page on the customer's own domain (website / email input).
SOURCE_COMPANY_WEBSITE = "COMPANY_WEBSITE"
#: Tier 2: an official corporate or public register.
SOURCE_OFFICIAL_REGISTER = "OFFICIAL_REGISTER"
#: Tier 4: a company directory, or a page nothing ties to the company.
SOURCE_SECONDARY = "SECONDARY"
PRIMARY_SOURCES = frozenset({
    SOURCE_COMPANY_IMPRESSUM, SOURCE_COMPANY_WEBSITE, SOURCE_OFFICIAL_REGISTER,
})

#: Official register hosts (registrable domain). Tier 2 — not directories.
REGISTER_DOMAINS: frozenset[str] = frozenset({
    "handelsregister.de", "unternehmensregister.de", "bundesanzeiger.de",
    "bzst.de", "e-justice.europa.eu",
})

# ── Flags (VAT_Found_Flags) ─────────────────────────────────────────────────
#: Prefix: the record's and the page's legal forms differ (``…:ag/gmbh``).
LEGAL_FORM_DIFFERS = "LEGAL_FORM_DIFFERS"
DOMAIN_MATCH = "DOMAIN_MATCH"
DOMAIN_MISMATCH = "DOMAIN_MISMATCH"
REGISTER_MATCH = "REGISTER_MATCH"
#: The page states a register entry of the same kind, and it is not ours.
REGISTER_MISMATCH = "REGISTER_MISMATCH"
#: Prefix: mandatory inputs (§2) the record lacks (``…:street,postal_code``).
INPUT_INCOMPLETE = "INPUT_INCOMPLETE"

#: §5's indicative weights, in percent.
WEIGHTS: dict[str, int] = {
    "name": 30, "street": 25, "postal_code": 15, "city": 10, "domain": 10, "register": 10,
}
#: Credit per name verdict. A branch or a partial name is half a name.
_NAME_CREDIT = {NAME_EXACT: 1.0, NAME_BRANCH: 0.5, NAME_PARTIAL: 0.5, NAME_MISMATCH: 0.0}


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

#: Long forms → canonical token, applied to folded text, longest first.
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
    ("limited", "ltd"),
    ("incorporated", "inc"),
)
#: Canonical legal-form tokens.
_FORMS = frozenset({
    "gmbh", "ggmbh", "ag", "kgaa", "kg", "ohg", "se", "ug", "ev", "eg", "kdoer", "adoer",
    "gbr", "ltd", "inc", "llc", "plc", "bv", "nv", "sa", "sarl", "sas", "spa", "srl",
})


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


def legal_form(name: str | None) -> str | None:
    """The canonical legal form(s) of *name*, in order (``"ag kgaa"`` for
    "Henkel AG & Co. KGaA"); None when it states none."""
    forms: list[str] = []
    for token in _form_tokens(name or ""):
        if token in _FORMS and token not in forms:
            forms.append(token)
    return " ".join(forms) or None


def legal_forms_differ(page_name: str | None, record_name: str | None) -> tuple[str, str] | None:
    """``(record form, page form)`` when both state a legal form and they
    differ; None otherwise."""
    page, record = legal_form(page_name), legal_form(record_name)
    if page and record and page != record:
        return record, page
    return None


# ---------------------------------------------------------------------------
# Address
# ---------------------------------------------------------------------------

#: A German address block: a 5-digit postcode followed by a capitalised word.
ADDRESS_BLOCK_RE = re.compile(r"(?<![\d-])(?:D-?\s?)?(\d{5})\s+(?=[A-ZÄÖÜ])([^\n]{0,40})")


_STREET_SUFFIX_RE = re.compile(r"\b([a-z]+?)\s?(?:strasse|str)\b")


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
    """The City (umlaut-folded either way, case-insensitive) on the page, or
    its longest token as the town of an address block ("FRANKFURT AM MAIN" vs
    "60549 Frankfurt")."""
    if not fold(city):
        return None
    blocks = [m.group(2) for m in ADDRESS_BLOCK_RE.finditer(page_text or "")]
    for umlaut_e in (True, False):
        sap_city = fold(city, umlaut_e=umlaut_e)
        if f" {sap_city} " in f" {fold(page_text, umlaut_e=umlaut_e)} ":
            return True
        main = max(sap_city.split(), key=len)
        if len(main) >= 4 and any(main in fold(b, umlaut_e=umlaut_e).split() for b in blocks):
            return True
    return False


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
    hits = {k for k, v in parts.items() if v}
    if hits == {"street", "postal_code", "city"}:
        level = ADDRESS_EXACT
    elif "postal_code" in hits and hits & {"street", "city"}:
        level = ADDRESS_STRONG
    elif hits:
        level = ADDRESS_PARTIAL
    elif ADDRESS_BLOCK_RE.search(page_text or ""):
        level = ADDRESS_NONE
    else:
        level = ADDRESS_NOT_STATED
    return level, parts


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
# Score and source type
# ---------------------------------------------------------------------------

def match_score(name: str, elements: dict[str, bool | None]) -> int:
    """§5's weighted score, 0–100, over the elements the record carries (an
    element with no input — no website, no register number — is left out of
    the denominator, so a record is not penalised for what SAP lacks)."""
    got = WEIGHTS["name"] * _NAME_CREDIT.get(name, 0.0)
    total = WEIGHTS["name"]
    for key, value in elements.items():
        if value is None:
            continue
        total += WEIGHTS[key]
        got += WEIGHTS[key] if value else 0
    return round(100 * got / total)


def is_register_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in REGISTER_DOMAINS)


def source_type(url: str, *, directory: bool, impressum: bool, own_domain: bool | None) -> str:
    """§4's hierarchy for the page a candidate was read on."""
    if is_register_host(url):
        return SOURCE_OFFICIAL_REGISTER
    if directory:
        return SOURCE_SECONDARY
    if impressum:
        return SOURCE_COMPANY_IMPRESSUM
    if own_domain:
        return SOURCE_COMPANY_WEBSITE
    return SOURCE_SECONDARY


# ---------------------------------------------------------------------------
# Candidate pattern (§6)
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
