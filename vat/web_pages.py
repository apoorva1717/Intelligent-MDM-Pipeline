"""Which pages the llm_web hybrid search fetches, and which page counts
(Steps AB–AD).

* :func:`select_pages` — of a search's results, only hosts that are the
  entity's own: (a) a distinctive word of the search name, or its initials,
  in the domain (for a SUB_ENTITY also the parent's; off for a GENERIC name);
  (b) the website a directory entry names (the directory page itself is
  never fetched); (c) another host's Impressum page in the results, fetched
  last, own only when it names the entity (VERIFY). A parent's host counts
  only when that parent is named in Name 1–4. One page per host; the
  blocklist is never fetched.
* :func:`follow_targets` — on an own host whose page states no VAT ID, the
  next page to open (Impressum, Kontakt, Steuern, Lieferanten, AGB …).
* :func:`host_check` — a host is rejected only when its legal-notice page
  does not name the entity, or both sides state a legal form and they
  differ. Another town never rejects.
* :func:`block_numbers` — on a page with several companies' numbers, which
  number sits in the block that names the entity.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from vat.entity_match import (
    _GENERIC_WORDS,
    company_domain,
    domain_match,
    fold,
    forms_on_page,
    has_address,
    is_aggregator_host,
    is_archive_host,
    is_register_host,
    is_rejected_host,
    parse_name,
    registrable_domain,
    vat_candidates,
)
from vat.name_prep import (
    GENERIC_WORDS,
    SUB_ENTITY,
    NamePlan,
    distinctive_tokens,
    forms_differ,
    prepare,
)

OWN = "own"
#: Kept for callers and old logs; a parent host now counts as OWN only when
#: the parent is named in Name 1–4 (and is name-checked like any own host).
PARENT = "parent"
#: The website a directory entry names for the entity.
POINTER = "pointer"
#: Another host's Impressum-type page in the results: fetched last, and own
#: only when the page names the entity (J&M Analytik on pharma-test.de).
VERIFY = "verify"
#: An institutional parent domain for the record's segment (europa.eu,
#: bund.de, a Land's portal, mpg.de …): own once its Impressum names the entity.
INSTITUTION = "institution"
#: A host the ranking model says belongs to the entity (fau.de, umg.eu)
#: without a token or initials match: own once its Impressum names the entity.
CANDIDATE = "candidate"
#: Kinds that count as own only after a legal-notice page names the entity.
UNVERIFIED = frozenset({VERIFY, INSTITUTION, CANDIDATE})
#: A legal-notice page whose notice names another company (with its own
#: register entry) while the title or menu names the entity: the parent's.
PARENT_CANDIDATE = "PARENT_CANDIDATE"

#: Pages on which the entity's name is checked (and a PDF): a homepage
#: without the legal name just means "follow the Impressum link".
_LEGAL_PAGE_RE = re.compile(
    r"impressum|imprint|legal|kontakt|contact|steuer|rechtlich|agb|about|ueber-uns|uber-uns|\.pdf$", re.I)
#: A legal-notice path for VERIFY candidates.
_NOTICE_PATH_RE = re.compile(r"impressum|imprint|legal[-_]?notice|anbieterkennzeichnung", re.I)
#: Rule 9: the pages that satisfy the search-2 gate (and stop the probe):
#: a real Impressum-type page, never Kontakt / Über uns.
_GATE_PAGE_RE = re.compile(
    r"impressum|imprint|legal|rechtliche[-_ ]?hinweise|anbieterkennzeichnung|steuer|\bagb\b|/agb", re.I)
#: Rule 13: what a legal notice says, as opposed to a title or a menu.
_LEGAL_BLOCK_RE = re.compile(
    r"anschrift|vertreten\s+durch|vertretungsberechtigt|registergericht|handelsregister|registernummer|"
    r"\bHR[AB]\b|ust\.?[\s-]*id|umsatzsteuer|gesch(?:ä|ae)ftsf(?:ü|ue)hr|vorstand|sitz\s+der\s+gesellschaft|"
    r"amtsgericht|diensteanbieter|herausgeber", re.I)
#: Hosts ranked last: job boards, shops, portals, logins, newsletters.
_LOW_HOST_RE = re.compile(r"^(?:jobs?|karriere|career|shop|portal|login|patienten\w*|international\w*|"
                          r"newsletter\w*)\.", re.I)
#: Rule 7: institutional parent domains, by segment.
_LAND_PORTALS = frozenset({
    "rlp.de", "bayern.de", "nrw.de", "baden-wuerttemberg.de", "sachsen.de", "niedersachsen.de", "hessen.de",
    "berlin.de", "hamburg.de", "brandenburg.de", "thueringen.de", "sachsen-anhalt.de", "schleswig-holstein.de",
    "saarland.de", "bremen.de", "mv-regierung.de", "regierung-mv.de",
})
_RESEARCH_DOMAINS = frozenset({"mpg.de", "fraunhofer.de"})
_EU_RE = re.compile(r"europ|kommission|\bEU\b|\bJRC\b", re.I)
_RESEARCH_RE = re.compile(r"institut|forschung|zentrum|max-planck|fraunhofer|helmholtz|leibniz|\bMPI\b", re.I)
#: Words never used for initials.
_STOPWORDS = frozenset({"und", "der", "die", "das", "des", "fuer", "f", "von", "zu", "am", "an", "im", "in", "the",
                        "and", "of", "for", "a"})
#: Heads of German compounds, for initials: Universitäts|klinikum → U K.
_COMPOUND_HEADS = ("klinikum", "medizin", "klinik", "institut", "zentrum", "akademie", "hochschule", "schule",
                   "amt", "anstalt", "polizei", "forschung", "gesellschaft", "technik", "werke")
#: "<name> <legal form>" on a page: the entities it names.
_ENTITY_ON_PAGE_RE = re.compile(
    r"((?:[A-ZÄÖÜ0-9][\w&.+'\-]*\s+){0,6}?[A-ZÄÖÜ0-9][\w&.+'\-]*)\s+"
    r"(gGmbH|GmbH|mbH|AG|SE|KGaA|KG|OHG|UG|e\.\s?V\.|eG|e\.\s?K\.|Stiftung|Aktiengesellschaft|"
    r"Kommanditgesellschaft|Gesellschaft mit beschr(?:ä|ae)nkter Haftung)\b")

#: Never fetched, even when they look like a source: benefit portals, trade
#: and study portals, tender aggregators, company pages of chemistry portals,
#: imprint generators, tax-advice sites, encyclopedias (class C if ever a
#: source). Social networks and marketplaces are entity_match's REJECTED_LABELS.
BLOCKED_LABELS = frozenset({
    "mitarbeiterangebote", "industryarena", "studi", "tenderinfo", "textileinfomedia", "kunststoffweb",
    "chemie", "chemeurope", "e-recht24", "erecht24", "impressum-generator", "impressumgenerator",
    "datenschutz-generator", "steuer-erklaerer", "steuertipps", "wikipedia", "wikimedia", "wikiwand",
})
#: Directories beyond entity_match.AGGREGATOR_LABELS: pointers only.
MORE_DIRECTORIES = frozenset({"bizdb", "webvalid", "firmania", "unternehmen24", "online-handelsregister",
                              "firmenwissen", "wer-zu-wem"})
#: Path words, best first: where a legal notice / tax data / supplier data sits.
_PATH_RANK = (
    re.compile(r"impressum|imprint|legal|rechtlich|anbieterkennzeichnung", re.I),
    re.compile(r"steuer|\bust\b|ust-?id|\bvat\b|\buid\b|companyinfo|company-info", re.I),
    re.compile(r"einkauf|lieferant|beschaffung|rechnung|supplier|procurement|agb|einkaufsbeding", re.I),
    re.compile(r"kontakt|contact|about|ueber-uns|uber-uns|unternehmen", re.I),
)
#: Links worth following on an own host, best first.
_FOLLOW_RANK = (
    re.compile(r"impressum|imprint|legal[\s_-]*notice|rechtliche[\s_-]*hinweise|anbieterkennzeichnung", re.I),
    re.compile(r"steuer|hochschulbesteuerung", re.I),
    re.compile(r"lieferant|einkauf|beschaffung|rechnungsanschrift", re.I),
    re.compile(r"\bagb\b|/agb", re.I),
    re.compile(r"kontakt|contact|(?:ü|ue|u)ber[\s_-]*uns|about", re.I),
)
#: Universities publish the USt-IdNr. on a tax page (Konstanz): first.
_UNIVERSITY_RE = re.compile(r"universit|hochschule", re.I)
_LANG_PREFIX_RE = re.compile(r"^/(?:en|de|en-us|en-gb|de-de|int)(?=/|$)", re.I)


def host_of(url: str | None) -> str:
    return (urlparse(url or "").hostname or "").lower().removeprefix("www.")


def _labels(url: str) -> list[str]:
    return host_of(url).split(".")


def _has(url: str, labels: frozenset[str]) -> bool:
    return any(label == b or (len(b) >= 6 and b in label) for label in _labels(url) for b in labels)


def is_blocked(url: str) -> bool:
    return _has(url, BLOCKED_LABELS)


def with_scheme(url: str | None) -> str:
    """A directory's website link as a URL: "www.kappler-cnc.de" → https://…"""
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")) and "." in url.split("/")[0]:
        return "https://" + url.lstrip("/")
    return url


def never_fetch(url: str) -> str | None:
    """Why a result is never fetched itself, or None."""
    if is_rejected_host(url):
        return "rejected source"
    if is_blocked(url):
        return "blocklist (class C)"
    if is_aggregator_host(url) or _has(url, MORE_DIRECTORIES):
        return "directory: pointer only"
    if is_register_host(url) or is_archive_host(url):
        return "register / archive"
    return None


def _plan_of(inp, plan: NamePlan | None) -> NamePlan:
    return plan or prepare(inp.names)


def city_tokens(city: str | None) -> frozenset[str]:
    """The record's city and its parts, folded (rule 6): never a word that
    identifies the entity's host (erlangen.de, braunschweig.de)."""
    parts = [p for p in re.split(r"[^a-z0-9]+", fold(city or "")) if len(p) >= 4]
    return frozenset(parts) | ({"".join(parts)} if len(parts) > 1 else frozenset())


_CITY_ENTITY_RE = re.compile(r"^\s*(?:stadt|gemeinde|landkreis|kreis|markt)\b", re.I)
#: Words naming a kind of institution, never one institution: no host
#: token ("karlshochschule.de" is not Hochschule Karlsruhe's).
_INSTITUTION_TYPE_WORDS = frozenset({
    "hochschule", "fachhochschule", "universitaet", "universitat", "university", "universitaetsklinikum",
    "klinikum", "uniklinikum", "universitaetsmedizin", "akademie", "stiftung", "kaserne", "polizei",
})
#: Rule 6's narrow exception: a domain that pairs the city with the record's
#: own institution type (uni-heidelberg.de for "Universität Heidelberg",
#: tu-dresden.de, hs-karlsruhe.de, ukm.de-style uk- labels).
_TYPE_PREFIXES: tuple[tuple[re.Pattern, frozenset[str]], ...] = (
    (re.compile(r"universit(?:ä|ae)tsklinikum|uniklinik|klinikum", re.I),
     frozenset({"uk", "ukl", "uniklinik", "uniklinikum", "klinikum"})),
    (re.compile(r"technische\s+universit", re.I), frozenset({"tu"})),
    (re.compile(r"universit(?:ä|ae)t|university", re.I), frozenset({"uni"})),
    (re.compile(r"hochschule", re.I), frozenset({"hs", "fh", "hochschule"})),
)
#: Label endings that do not change whose host it is ("bmwgroup", "acl-online").
_LABEL_SUFFIXES = frozenset({"group", "gruppe", "online", "web", "net", "info", "de", "deutschland", "germany",
                             "ag", "gmbh"})
_FORM_PHRASE_RE = re.compile(r"\b(?:stiftung|anstalt|k(?:ö|oe)rperschaft)\s+(?:des\s+)?\S+\s+rechts\b", re.I)


def _split_compound(word: str) -> list[str]:
    for head in _COMPOUND_HEADS:
        if word.endswith(head) and len(word) > len(head) + 2:
            return [*_split_compound(word[:-len(head)]), head]
    return [word]


def name_acronyms(name: str | None) -> frozenset[str]:
    """Rule 4: the name's acronyms of ≥ 3 letters: one written in capitals
    (FAU, NMI, ACL), and its initials, with and without generic words and
    with German compounds split (Universitätsklinikum Münster → UKM,
    Universitätsmedizin Göttingen → UMG, Karlsruher Institut für Technologie → KIT)."""
    raw = _FORM_PHRASE_RE.sub(" ", name or "")
    out = {fold(t) for t in re.findall(r"(?<![\w-])[A-ZÄÖÜ]{3,6}(?![\w-])", raw)
           if not parse_name(t).form and fold(t) not in GENERIC_WORDS}
    words = [w for w in parse_name(raw, sap=True).core if w and w not in _STOPWORDS]
    variants = (words, [w for w in words if w not in _GENERIC_WORDS],
                [p for w in words for p in _split_compound(w)])
    for v in variants:
        initials = "".join(w[0] for w in v)
        if 3 <= len(initials) <= 6:
            out.add(initials)
    return frozenset(out)


def resembles(url: str, name: str | tuple[str, ...], *, city: str | None = None) -> bool:
    """The domain carries a distinctive word of *name* (≥ 4 letters, not
    generic, not the record's *city*: rule 6), a label and a word one of
    which begins the other with ≥ 6 letters in common (rule 5:
    bundespolizei ↔ Bundespolizeiabteilung), or one of the name's acronyms
    as a label part (rule 4: ``fau.de``, ``umg.eu``, ``kit.edu``)."""
    domain = registrable_domain(url) or ""
    label = domain.rsplit(".", 1)[0]
    if not label:
        return False
    flat = re.sub(r"[^a-z0-9]", "", label)
    parts = [p for p in re.split(r"[^a-z0-9]+", label) if p]
    excluded = city_tokens(city)
    if isinstance(name, tuple):
        distinct = [w for w in name if len(w) >= 4 and w not in GENERIC_WORDS]
        acronyms = frozenset({"".join(w[0] for w in name if w not in _GENERIC_WORDS)} - {""})
        acronyms = frozenset(a for a in acronyms if len(a) >= 3)
    else:
        distinct, acronyms = distinctive_tokens(name), name_acronyms(name)
        # The city counts with the record's own institution type beside it.
        allowed = next((p for rx, p in _TYPE_PREFIXES if rx.search(name)), frozenset())
        if excluded and allowed and any(p in allowed for p in parts) and any(c in parts for c in excluded):
            return True
    distinct = [w for w in distinct if w not in excluded and w not in _INSTITUTION_TYPE_WORDS]
    if any(w in flat for w in distinct):
        return True
    if any(min(len(p), len(w)) >= 6 and (p.startswith(w) or w.startswith(p)) and p not in excluded
           for p in parts for w in distinct):
        return True
    return acronym_in_label(acronyms, parts)


def acronym_in_label(acronyms, parts) -> bool:
    """An acronym is a label part, or begins one followed only by a word that
    does not change whose host it is ("bmwgroup", "acl-online")."""
    return any(p == a or (p.startswith(a) and p[len(a):] in _LABEL_SUFFIXES) for a in acronyms for p in parts)


def institutional_kind(url: str, plan: NamePlan) -> str | None:
    """Rule 7: INSTITUTION when *url* is an institutional parent domain for
    the record's segment: europa.eu for an EU body, bund.de for a federal one,
    a Land's portal for a state one, mpg / fraunhofer / helmholtz* / leibniz*
    for a research institute."""
    domain = registrable_domain(url) or ""
    names = " ".join(n for n in (plan.entity, *plan.names, *plan.context) if n)
    if domain == "europa.eu":
        return INSTITUTION if _EU_RE.search(names) else None
    if domain == "bund.de":
        return INSTITUTION if plan.public and re.search(r"bundes", names, re.I) else None
    if domain in _LAND_PORTALS:
        return INSTITUTION if plan.public and not re.search(r"\bbundes", names, re.I) else None
    label = domain.split(".")[0]
    if domain in _RESEARCH_DOMAINS or label.startswith(("helmholtz", "leibniz")):
        return INSTITUTION if _RESEARCH_RE.search(names) else None
    return None


def path_rank(url: str) -> int:
    path = urlparse(url).path
    for i, rx in enumerate(_PATH_RANK):
        if rx.search(path):
            return i
    return len(_PATH_RANK)


def page_key(url: str) -> tuple[str, str]:
    """Host and path with a language prefix dropped and "imprint" read as
    "impressum": ``/en/imprint`` and ``/impressum`` are the same page."""
    path = _LANG_PREFIX_RE.sub("", urlparse(url).path or "/").lower().rstrip("/") or "/"
    path = re.sub(r"imprint|legal-notice|legal_notice", "impressum", path)
    return host_of(url), path


def host_kind(url: str, inp, *, title: str | None = None, plan: NamePlan | None = None, **_) -> str | None:
    """OWN or None for one result URL (rule (a) and the title rule). The
    company's own domain always counts. A GENERIC name has no token rule. A
    result titled with the full name counts on a legal-notice path only (a
    directory's page about the company carries its name in the title too)."""
    plan = _plan_of(inp, plan)
    if domain_match(url, company_domain(inp.website, inp.email)):
        return OWN
    # Rule 6: the city is no identifying word, unless the city is the entity.
    city = None if _CITY_ENTITY_RE.match(plan.entity or "") else getattr(inp, "city", None)
    if not plan.generic:
        names = [plan.search_name, plan.entity]
        if plan.parent and (plan.cls == SUB_ENTITY or plan.reverse):
            names.append(plan.parent)  # the umbrella named in Name 1–4
        if any(resembles(url, n, city=city) for n in names if n):
            return OWN
    else:
        # A GENERIC name has no token rule; an acronym written in capitals
        # still identifies its host ("ACL GmbH" → acl-online.de).
        caps = {fold(t) for t in re.findall(r"(?<![\w-])[A-ZÄÖÜ]{3,6}(?![\w-])", plan.entity or "")
                if not parse_name(t).form}
        label = (registrable_domain(url) or "").rsplit(".", 1)[0]
        if acronym_in_label(caps, [p for p in re.split(r"[^a-z0-9]+", label) if p]):
            return OWN
    full = fold(" ".join(parse_name(plan.entity, sap=True).core))
    if title and full and len(full) >= 6 and full in fold(title) and path_rank(url) == 0:
        return OWN
    return None


def is_legal_page(url: str) -> bool:
    return bool(_LEGAL_PAGE_RE.search(urlparse(url).path))


def is_gate_page(url: str) -> bool:
    """Rule 9: an Impressum-type page (impressum, imprint, legal, legal-notice,
    rechtliche-hinweise, steuer*, agb): the only kind whose reading with
    HTTP 200 on an own host means the host answered."""
    return bool(_GATE_PAGE_RE.search(urlparse(url).path))


def legal_block(text: str | None) -> str | None:
    """Rule 13: the legal notice of a page — from 500 characters before its
    first notice term (Anschrift, Vertreten durch, Registergericht, HRB,
    USt-IdNr …) to 300 after its last; None when the page has none."""
    marks = list(_LEGAL_BLOCK_RE.finditer(text or ""))
    if not marks:
        return None
    return text[max(0, marks[0].start() - 500):marks[-1].end() + 300]


def initials_match(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """*a* is *b* with a run of words written as their initials, or the other
    way round, generic words ignored: ("hp", "deutschland") for ("hewlett",
    "packard", "deutschland"); ("bmw",) for ("bayerische", "motoren", "werke")."""
    def plain(words):
        return tuple(w for w in words if w not in GENERIC_WORDS)

    for short, long in ((a, b), (b, a)):
        for i, token in enumerate(short):
            for j in range(len(long)):
                for k in range(j + 2, len(long) + 1):
                    # Initials over every word (BMW: Bayerische Motoren Werke),
                    # the rest compared without generic words ("Group").
                    if "".join(w[0] for w in long[j:k]) == token and \
                            plain((*short[:i], *long[j:k], *short[i + 1:])) == plain(long):
                        return True
    return False


def _words_present(name: str, folded: str, *, strict: bool = False) -> bool:
    """The first distinctive word of *name*, and at least half of them, are
    in the page: "KACO GmbH & Co KG Dichtungswerke" is "KACO GmbH + Co. KG".
    *strict* (one block of a group page): all of them."""
    words = [w for w in parse_name(name, sap=True).core if w not in _GENERIC_WORDS and len(w) >= 3] \
        or list(parse_name(name, sap=True).core)
    present = [w for w in words if w in folded]
    if strict:
        return bool(words) and len(present) == len(words)
    return bool(words) and words[0] in present and 2 * len(present) >= len(words)


def entities_on_page(text: str) -> list[str]:
    """Every "<words> <legal form>" on the page, each trailing run of words
    ("Impressum HP Deutschland GmbH" → … "HP Deutschland GmbH")."""
    out = []
    for m in _ENTITY_ON_PAGE_RE.finditer(text or ""):
        words_before = m.group(1).split()
        out += [" ".join([*words_before[i:], m.group(2)]) for i in range(len(words_before))]
    return out


def name_on_page(text: str, names, alt_names=(), *, strict: bool = False) -> str | None:
    """How the page names the entity: ``EXACT`` (one of *names*: its
    distinctive words, or a name the matcher calls EXACT, a branch /
    department variant included), ``ABBREVIATED`` (initials), ``NAME_USED``
    (the name the model searched with in search 2); None when it names none.
    *names*: the acceptable names (a :class:`NamePlan`'s), or a LookupInput."""
    from vat.entity_match import NAME_EXACT, entity_name_on_page, name_match

    if hasattr(names, "names"):  # a LookupInput
        names = [n for n in (names.name_1,) if n]
    folded = fold(text)
    for name in names:
        if _words_present(name, folded, strict=strict) or entity_name_on_page(text, (name, None, None, None)):
            return "EXACT"
    candidates = entities_on_page(text)
    for name in names:
        record = parse_name(name, sap=True).core
        for cand in candidates:
            if name_match(cand, (name, None, None, None)).match == NAME_EXACT:
                return "EXACT"
            if initials_match(parse_name(cand).core, record):
                return "ABBREVIATED"
    for alt in alt_names:
        alt_words = [w for w in parse_name(alt).core if w not in _GENERIC_WORDS and len(w) >= 3]
        if alt_words and all(w in folded for w in alt_words):
            return "NAME_USED"
    return None


def select_pages(
    results: list[dict], inp, *, done_hosts=frozenset(), plan: NamePlan | None = None, **_,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """``(picked, skipped)``: one ``(url, kind)`` per own / directory-linked
    host in result order, then hosts to VERIFY; each with its best-ranked
    path (the German page before its language twin); and ``(url, reason)``
    for every result not fetched. *results*: dicts with ``url`` and
    optionally ``title`` / ``website`` (a directory entry's link to the
    entity's website)."""
    plan = _plan_of(inp, plan)
    by_host: dict[str, list[tuple[int, str, str]]] = {}
    order: list[str] = []
    skipped: list[tuple[str, str]] = []

    def add(url: str, kind: str, pos: int) -> None:
        host = host_of(url)
        if host in done_hosts:
            skipped.append((url, "host already fetched or rejected"))
            return
        if host not in by_host:
            by_host[host] = []
            order.append(host)
        by_host[host].append((pos, url, kind))

    for pos, r in enumerate(results):
        url = (r.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        reason = never_fetch(url)
        website = with_scheme(r.get("website"))
        if reason:
            skipped.append((url, reason))
            if reason.startswith("directory") and website.startswith(("http://", "https://")) \
                    and not never_fetch(website):
                add(website, POINTER, pos)
            continue
        kind = host_kind(url, inp, title=r.get("title"), plan=plan) or institutional_kind(url, plan)
        if kind is None and _NOTICE_PATH_RE.search(urlparse(url).path):
            kind = VERIFY  # own if its Impressum names the entity
        if kind is None:
            skipped.append((url, "not the entity's own host"))
            continue
        add(url, kind, pos)

    picked: list[tuple[str, str]] = []
    first: dict[str, int] = {}
    for host in order:
        entries = by_host[host]
        kind = OWN if any(k == OWN for _, _, k in entries) else entries[0][2]
        best = min(entries, key=lambda e: (path_rank(e[1]), bool(_LANG_PREFIX_RE.match(urlparse(e[1]).path)
                                                                   and not urlparse(e[1]).path.startswith("/de")), e[0]))
        picked.append((best[1], kind))
        first[best[1]] = min(e[0] for e in entries)
        skipped += [(u, "one page per host") for _, u, _ in entries if u != best[1]]
    picked.sort(key=lambda p: heuristic_rank(p[0], p[1], first[p[0]]))
    return picked, skipped


#: Rule 8's order of kinds: own and directory-linked hosts, then
#: institutional parents and model candidates, then other hosts' Impressum pages.
_KIND_ORDER = {OWN: 0, POINTER: 0, PARENT: 0, INSTITUTION: 1, CANDIDATE: 1, VERIFY: 2}


def heuristic_rank(url: str, kind: str, pos: int) -> tuple:
    """Rule 8 (the fallback ranking): an Impressum-type path first, then a
    directory's link, then the shortest host, then result order; job, shop,
    portal, login, patient, international and newsletter hosts last."""
    host = host_of(url)
    return (_KIND_ORDER.get(kind, 2), bool(_LOW_HOST_RE.match(host)), path_rank(url) != 0, kind != POINTER,
            len(host), pos)


def follow_targets(
    page_url: str, links: list, result_urls: list[str], fetched: set[tuple[str, str]],
    *, university: bool = False,
) -> list[str]:
    """Pages on the host of *page_url* to open next, best first: result URLs
    and page links whose text or path is an Impressum / Steuern / Lieferanten
    / Einkauf / Rechnungsanschrift / AGB / Kontakt / Über uns link (for a
    university the tax page first), then ``/impressum``. Language twins of a
    fetched page are skipped."""
    host = host_of(page_url)
    ranks = list(_FOLLOW_RANK)
    if university:
        ranks.insert(0, ranks.pop(1))  # Steuernummern / Hochschulbesteuerung
    scored: list[tuple[int, bool, int, str]] = []
    candidates = [("", u) for u in result_urls] + [(t or "", h) for t, h in links or []]
    for pos, (text, href) in enumerate(candidates):
        url = urljoin(page_url, href or "").split("#")[0]
        if not url.startswith(("http://", "https://")) or host_of(url) != host:
            continue
        for rank, rx in enumerate(ranks):
            if rx.search(text) or rx.search(urlparse(url).path):
                twin = bool(_LANG_PREFIX_RE.match(urlparse(url).path)) or "imprint" in url.lower()
                scored.append((rank, twin, pos, url))
                break
    seen: set[tuple[str, str]] = set(fetched)
    out = []
    for *_, url in sorted(scored):
        if page_key(url) not in seen:
            seen.add(page_key(url))
            out.append(url)
    p = urlparse(page_url)
    probe = f"{p.scheme or 'https'}://{p.hostname}/impressum"
    if page_key(probe) not in seen:
        out.append(probe)
    return out


def is_university(plan: NamePlan) -> bool:
    return bool(_UNIVERSITY_RE.search(plan.entity))


#: The extended probe's paths after ``/impressum`` and ``/imprint``; ``{lang}``
#: is the language prefix of the page the host was reached on ("/de-pl").
_EXTENDED_PROBES = ("{lang}/impressum", "/de/impressum", "/de-de/impressum", "/impressum.html", "/legal-notice",
                    "/en/imprint")
_PAGE_LANG_RE = re.compile(r"^/([a-z]{2}(?:[-_][a-z]{2})?)(?=/)", re.I)


def impressum_probes(page_url: str, fetched: set[tuple[str, str]], *, extended: bool = False) -> list[str]:
    """``/impressum`` and ``/imprint`` on the host of *page_url*, unless
    fetched. *extended* (VAT_WEB_EXTENDED_PROBES): then the language and file
    variants of :data:`_EXTENDED_PROBES` (TFP Fertility: /de-pl/ pages, no
    root /impressum)."""
    p = urlparse(page_url)
    paths = ["/impressum", "/imprint"]
    if extended:
        lang = _PAGE_LANG_RE.match(p.path or "")
        paths += [x.format(lang=f"/{lang.group(1)}" if lang else "") for x in _EXTENDED_PROBES]
    out = []
    for path in dict.fromkeys(paths):
        url = f"{p.scheme or 'https'}://{p.hostname}{path}"
        if (host_of(url), path) not in fetched:
            out.append(url)
    return out


def is_empty_legal_page(url: str, text: str) -> bool:
    """A legal-notice page that came back without its content (rendered by
    script): no address and no VAT / register mention (TIB Chemicals)."""
    from vat.lookup_grounded import _VAT_KEYWORD_RE

    return bool(_NOTICE_PATH_RE.search(urlparse(url).path)) and not has_address(text) \
        and not _VAT_KEYWORD_RE.search(text or "") and not re.search(r"\bHR[AB]\b", text or "")


def host_check(text: str, url: str, inp, kind: str, alt_names=(), plan: NamePlan | None = None,
               ) -> tuple[str | None, str]:
    """``(why the host is rejected or None, how the page names the entity)``.

    Rejected only when a legal-notice / contact / tax / AGB / about page or
    PDF does not name the entity (for a SUB_ENTITY: the sub-entity; the
    parent's Impressum fails for it); on an Impressum-type page the legal
    notice itself must name it (rule 13), and a notice that names another
    company (usually with its own HRB) while the title or menu names the
    entity is :data:`PARENT_CANDIDATE`: not rejected, its number for review. Another legal form never rejects (Step AF: see
    :func:`form_difference`), nor does another town: the matcher's
    LEGAL_FORM_DIFFERS / ADDRESS_MISMATCH handle them. A homepage is not
    name-checked."""
    plan = _plan_of(inp, plan)
    if not text:
        return None, "NOT_CHECKED"
    if not is_legal_page(url):
        return None, "NOT_CHECKED"
    # Rule 13: on an Impressum-type page, the legal notice must name the
    # entity, not just the title or the menu.
    block = legal_block(text) if is_gate_page(url) else None
    if block is not None:
        named = name_on_page(block, plan.acceptable, alt_names)
        if named is not None:
            return None, named
        if name_on_page(text, plan.acceptable, alt_names) is not None and entities_on_page(block):
            # Diakonie Klinikum Kirchen's page, Diakonie in Südwestfalen gGmbH's
            # notice (HRB 3432): a number there is the other company's.
            return None, PARENT_CANDIDATE
        return "NAME_NOT_ON_PAGE", "ABSENT"
    named = name_on_page(text, plan.acceptable, alt_names)
    if named is None:
        return "NAME_NOT_ON_PAGE", "ABSENT"
    return None, named


def form_difference(text: str, plan: NamePlan) -> str | None:
    """``"<sap>/<page>"`` when the page states the entity's name with another
    legal form and SAP states one too (both sides; an UNKNOWN_GMBH_FAMILY or
    a missing form never differs). Not a rejection: the number becomes a
    candidate for review (LEGAL_FORM_DIFFERS)."""
    if not plan.form or not text:
        return None
    core = parse_name(plan.entity, sap=True).core
    page_forms = forms_on_page(text, core)
    # A foundation writes its form first ("Stiftung kreuznacher diakonie"),
    # sometimes as part of the name itself.
    named = r"\s+".join(map(re.escape, core))
    if core and (re.search(r"\bstiftung\s+" + named + r"\b", fold(text))
                 or (core[0] == "stiftung" and re.search(r"\b" + named + r"\b", fold(text)))):
        page_forms.add("stiftung")
    if page_forms and all(forms_differ(plan.form, f) for f in page_forms):
        return f"{plan.form}/{'/'.join(sorted(page_forms))}"
    return None




def block_numbers(text: str, names, alt_names=()) -> tuple[list[str], list[str]]:
    """``(numbers in a block that names the entity, every number)`` on a page:
    each number's block is the text since the previous number (at most 600
    characters before it). A group page (hospital chain, holding, industrial
    park) lists several companies, each with its number."""
    hits = vat_candidates(text)
    if len(hits) < 2:
        return list(hits), list(hits)
    passing, start = [], 0
    for vat in hits:
        digits = vat[2:]
        m = re.search(r"DE[ ]?" + "".join(d + r"[ .]?" for d in digits[:-1]) + digits[-1], text[start:])
        if not m:
            continue
        end = start + m.start()
        block = text[max(start, end - 600):end]
        if name_on_page(block, names, alt_names, strict=True):
            passing.append(vat)
        start = start + m.end()
    return passing, list(hits)


# ── The company a page gives a number to (2026-11-06) ───────────────────────

_HOLDER_WORD = r"[A-ZÄÖÜ0-9][\w&.+'\-]*"
_HOLDER_JOINER = r"(?:für|fuer|und|der|des|die|von|zu|&|-|–)"
_HOLDER_FORM = (r"(?:gGmbH|GmbH|AG|SE|UG)\s?&\s?Co\.\s?(?:KGaA|KG|OHG)|gGmbH|GmbH|mbH|AG|SE|KGaA|KG|OHG"
                r"|UG(?:\s?\(haftungsbeschränkt\))?|e\.\s?V\.|eG|e\.\s?K(?:fm|fr)?\.|Stiftung|Aktiengesellschaft")
#: "<words> <legal form>" on one line: lower-case joiners ("Gesellschaft für
#: Chemische Technik und Biotechnologie e.V."), "& Co. KG", "e. V.", "e.Kfm.".
_HOLDER_RE = re.compile(
    rf"({_HOLDER_WORD}(?:[ \t]+(?:{_HOLDER_WORD}|{_HOLDER_JOINER})){{0,10}}?)[ \t]*,?[ \t]+({_HOLDER_FORM})(?!\w)")
#: Where an Impressum's own entity follows.
_HOLDER_ANCHOR_RE = re.compile(
    r"impressum|imprint|legal notice|angaben gem(?:ä|ae)(?:ß|ss)|anschrift|herausgeber|anbieter", re.I)
#: The general partner or a representative, not the holder of the number.
_HOLDER_SKIP_RE = re.compile(r"komplement|persönlich haftende|vertreten durch|gesellschafterin|geschäftsführende", re.I)
_HOLDER_NOISE = frozenset({"impressum", "imprint", "kontakt", "kontaktadressen", "anschrift", "name", "und",
                           "office", "ddg", "tmg", "herausgeber", "anbieter", "menu", "menue", "home"})


def _number_re(vat: str) -> re.Pattern:
    digits = vat[2:]
    return re.compile(r"DE[ ]?" + "".join(d + r"[ .]?" for d in digits[:-1]) + digits[-1])


#: Characters of page text kept before and after a number in its snippet.
SNIPPET_BEFORE, SNIPPET_AFTER = 240, 80


def number_snippet(text: str | None, vat_id: str | None) -> str | None:
    """The page text around *vat_id* as fetched here (whitespace folded):
    where the number stands, whoever picked it. None when it is not on the page."""
    vat = re.sub(r"[\s.]", "", (vat_id or "")).upper()
    hit = _number_re(vat).search(text or "") if len(vat) > 3 else None
    if hit is None:
        return None
    start, end = max(0, hit.start() - SNIPPET_BEFORE), hit.end() + SNIPPET_AFTER
    snippet = " ".join(text[start:end].split())
    return ("…" if start else "") + snippet + ("…" if end < len(text) else "")


def number_holder(text: str | None, vat_id: str | None, names=()) -> str | None:
    """The company a page names for *vat_id* when no model said whose number
    it is (EXTRACTED_BY_PATTERN, a number picked from a group page): the
    first "<name> <legal form>" of its legal notice before the number, since
    the previous number, at most 1500 characters back. A general partner
    (Komplementärin) is skipped; words before the name are cut at the first
    word of SAP's name (*names*), else noise words ("Impressum", "Kontakt").
    So the matcher sees "Refratechnik Holding GmbH" for Refratechnik Ceramics
    GmbH. None when the page writes no such name."""
    vat = re.sub(r"[\s.]", "", (vat_id or "")).upper()
    hit = _number_re(vat).search(text or "") if len(vat) > 3 else None
    if hit is None:
        return None
    region = text[max(0, hit.start() - 1500):hit.start()]
    ends = [m.end() for v in vat_candidates(region) for m in _number_re(v).finditer(region)]
    if ends:
        region = region[max(ends):]
    for anchor in reversed(list(_HOLDER_ANCHOR_RE.finditer(region))):
        if _HOLDER_RE.search(region[anchor.end():]):
            region = region[anchor.end():]
            break
    sap = {w for w in fold(" ".join(str(n) for n in names if n)).split() if w not in _HOLDER_NOISE}
    for m in _HOLDER_RE.finditer(region):
        if _HOLDER_SKIP_RE.search(region[max(0, m.start() - 60):m.start()]):
            continue
        words = m.group(1).split()
        first = next((i for i, w in enumerate(words) if fold(w) in sap), None)
        if first is not None:
            words = words[first:]
        else:
            while len(words) > 1 and (fold(words[0]).strip(":") in _HOLDER_NOISE or words[0][0].isdigit()):
                words = words[1:]
        return " ".join([*words, m.group(2)])
    return None


# ── Step AH: the operator of a page's site, and a defunct entity ────────────

#: Rule AH-2: the legal notice names another organisation as the site's
#: operator, Träger or parent; the organisation's name follows the phrase.
_OPERATOR_RE = re.compile(
    r"(?:eine?\s+gemeinsame\s+Einrichtung\s+(?:der|des|von)|eine?\s+(?:zentrale\s+)?Einrichtung\s+(?:der|des)"
    r"|Betreiber(?:in)?(?:\s+(?:der|dieser)\s+(?:Web)?(?:seite|site|Website|Internetseite))?\s*:"
    r"|Träger(?:in)?\s*:|ist\s+eine?\s+(?:Unternehmen|Tochter(?:gesellschaft|unternehmen)?|Gesellschaft|Marke)\s+der"
    r"|(?:a\s+)?joint\s+(?:initiative|institution|facility)\s+of(?:\s+the)?"
    r"|(?:an\s+)?organi[sz]ational\s+unit\s+(?:within|of)(?:\s+the)?"
    r"|(?:is\s+)?operated\s+by(?:\s+the)?|(?:is\s+)?a\s+(?:company|subsidiary)\s+of(?:\s+the)?)"
    r"\s*(?P<name>[^\n.;:]{3,120})", re.I)
_OPERATOR_STOP_RE = re.compile(r"\s+(?:and|und|sowie|as|im\s+Sinne|gemäß|nach|within|innerhalb)\s+|\s*,\s*", re.I)
_IMPRINT_ANCHOR_RE = re.compile(r"impressum|imprint|legal\s*notice|rechtliche", re.I)


def _operator_tokens(name: str) -> tuple[set[str], set[str]]:
    """Distinctive words (≥ 4 letters) and acronyms (in parentheses, or the
    initials) of an operator's name."""
    acronyms = {a.lower() for a in re.findall(r"\(([A-ZÄÖÜ][A-Za-zÄÖÜ\-]{1,9})\)", name)}
    words = [w for w in re.findall(r"[A-Za-zÄÖÜäöüß]+", fold(re.sub(r"\([^)]*\)", " ", name)))]
    distinct = {w for w in words if len(w) >= 4 and w not in GENERIC_WORDS and w not in _GENERIC_WORDS}
    initials = "".join(w[0] for w in words if w not in _GENERIC_WORDS and len(w) > 2)
    if len(initials) >= 3:
        acronyms.add(initials)
    return distinct, acronyms


def operator_link(text: str | None, links, page_url: str) -> tuple[str, str] | None:
    """``(operator name, its Impressum URL)`` when the page's legal notice
    names another organisation as operator / Träger / parent ("a Joint
    Initiative of the University Medical Center Göttingen (UMG)", "Träger:",
    "ist ein Unternehmen der …") and a link on the page leads to that
    organisation's site: its imprint link when the page has one ("Click here
    for the imprint of UMG"), else ``<its host>/impressum``. None otherwise."""
    block = legal_block(text) or (text or "")[:4000]
    own = registrable_domain(page_url)
    for m in _OPERATOR_RE.finditer(block):
        name = _OPERATOR_STOP_RE.split(m.group("name").replace("↗", " ").strip(), maxsplit=1)[0].strip()
        full = m.group("name")
        distinct, acronyms = _operator_tokens(full if "(" in full[:len(name) + 12] else name)
        distinct |= _operator_tokens(name)[0]
        if not (distinct or acronyms):
            continue
        best: tuple[int, str] | None = None
        for anchor, href in links or ():
            href = urljoin(page_url, href or "")
            if not href.startswith(("http://", "https://")) or registrable_domain(href) == own or never_fetch(href):
                continue
            label = (registrable_domain(href) or "").rsplit(".", 1)[0]
            anchor_f = fold(anchor or "")
            hit = any(a in re.split(r"[^a-z0-9]+", anchor_f) or a == label for a in acronyms) \
                or any(w in anchor_f or w in label for w in distinct)
            if not hit:
                continue
            score = 2 if _IMPRINT_ANCHOR_RE.search(anchor or "") or is_gate_page(href) else 1
            if best is None or score > best[0]:
                target = href if score == 2 else f"{urlparse(href).scheme}://{urlparse(href).netloc}/impressum"
                best = (score, target)
        if best:
            return name, best[1]
    return None


#: Rule AH-4: the page says the entity no longer trades as itself.
_DEFUNCT_RE = re.compile(
    r"\b(?:in\s+Insolvenz|Insolvenzverfahren|insolvent|aufgelöst|gelöscht|erloschen|in\s+Liquidation|i\.\s?L\.|"
    r"in\s+Abwicklung|verschmolzen|umfirmiert|merged\s+(?:into|with)|dissolved|liquidated|ceased\s+(?:trading|operations))",
    re.I)


def defunct_statement(text: str | None, names) -> str | None:
    """A passage (≤ 200 characters) where the page says the entity is
    insolvent, dissolved, deleted or merged, within 200 characters of one
    of its distinctive words; None otherwise."""
    text = text or ""
    tokens = set()
    for n in names or ():
        if n:
            tokens |= set(distinctive_tokens(n))
    if not tokens:
        return None
    for m in _DEFUNCT_RE.finditer(text):
        lo, hi = max(0, m.start() - 200), min(len(text), m.end() + 200)
        if any(t in fold(text[lo:hi]) for t in tokens):
            return " ".join(text[max(0, m.start() - 100):m.end() + 100].split())
    return None


def operator_host_matches(url: str, name: str, text: str | None) -> bool:
    """Own-host rules on an operator's page: its domain carries the
    operator's name or acronym (``umg.eu`` for "… Göttingen (UMG)"), or the
    page names the operator."""
    distinct, acronyms = _operator_tokens(name)
    label = (registrable_domain(url) or "").rsplit(".", 1)[0]
    parts = set(re.split(r"[^a-z0-9]+", label))
    if acronyms & parts or resembles(url, name):
        return True
    return bool(name_on_page(text or "", (re.sub(r"\s*\([^)]*\)", "", name).strip(),)))
