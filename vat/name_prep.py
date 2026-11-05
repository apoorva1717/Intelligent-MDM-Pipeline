"""Name preparation for the missing-VAT lookup (Step AD, part 1).

Deterministic, before any search: what the record's legal entity is called,
which name the search uses and which names a page may show for it.

1. :func:`clean` — Name 2–4 without "c/o …", "z. Hd. …", "Attn …", a contact
   person's name, notes (">> 123") and numbers. The contact is kept apart.
2. :func:`legal_form_of` — the legal form, spellings folded; a form SAP's
   35-character cut left unreadable ("… GmbH & Co", "… Gmbh&") is
   :data:`UNKNOWN_GMBH_FAMILY`, which never differs from another form.
3. :func:`prepare` — Name 2–4 is exactly one of SUB_ENTITY, CONTINUATION,
   CONTINUATION_TRUNCATED (a visible cut), DEPARTMENT, CONTINUATION_TRUNCATED
   (Name 1 at SAP's limit), OTHER (tested in that order; a site in Name 2 is
   a BRANCH of Name 1); a department written first
   with its parent after it (reverse order) is the parent's DEPARTMENT; site
   words in Name 1 are a BRANCH; a name with no distinctive word is GENERIC;
   public bodies are recognised by name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from vat.entity_match import SAP_NAME_LIMIT, fold, parse_name

SUB_ENTITY = "SUB_ENTITY"
CONTINUATION = "CONTINUATION"
#: Name 1 cut at SAP's limit (or visibly unfinished) and Name 2 carries on
#: the name: the two joined (Step AF rule 10).
CONTINUATION_TRUNCATED = "CONTINUATION_TRUNCATED"
DEPARTMENT = "DEPARTMENT"
OTHER = "OTHER"
#: Issue / flag codes.
BRANCH = "BRANCH"
GENERIC_NAME = "GENERIC_NAME"
UNKNOWN_GMBH_FAMILY = "UNKNOWN_GMBH_FAMILY"

# ── 1.1 Cleaning ─────────────────────────────────────────────────────────────
_CARE_OF_RE = re.compile(
    r"(?:^|\s|/)(?:c/o|c\.o\.|z\.\s?hd\.?|z\.\s?h\.|zhd\.?|attn\.?:?|attention:?|herrn?|frau|hr\.|fr\.)"
    r"(?=\s|$|:)"
    r".*$", re.IGNORECASE)
_NOTE_RE = re.compile(r"^\s*(?:>>.*|[\d\s./-]+)$")
_TITLE_RE = re.compile(r"^(?:(?:dr|prof|dipl\.?-?\s?ing|dipl|ing)\.[-\s]*)+", re.IGNORECASE)
#: Common first names (folded): a Name 2–4 that is a first name + a surname,
#: with no legal form, is a contact person.
FIRST_NAMES = frozenset("""
alexander andreas anna anne andrea angelika anja annette antje axel barbara bernd bernhard
birgit bettina brigitte carsten christa christian christiane christina christine christoph
claudia cornelia daniel daniela david dennis detlef dieter dietmar dirk doris dorothea
eberhard edith elke elisabeth emil erich erika ernst eva fabian felix florian frank franz
friedrich gabriele georg gerd gerhard gisela gudrun guenter guenther gunnar hannah hans
harald heike heinrich heinz helga helmut herbert holger horst hubert ilse ines ingrid
irene iris jan jana jens joachim jochen johann johannes jonas joerg josef juergen julia
julian juliane jutta kai karin karl karsten katharina kathrin katja klaus konrad kurt
lars laura lena lisa lothar lukas lutz manfred manuela marc marco marcus maria marie
marina mario mark markus marlies martin martina matthias max maximilian melanie michael
michaela monika nadine nicole nicolai nikolai nils nina norbert olaf oliver otto patrick
paul peter petra philipp rainer ralf ralph regina reinhard renate rene richard robert
roland rolf ruediger ruth sabine sabrina sandra sarah sascha sebastian silke simon
simone sonja stefan stefanie steffen stephan susanne sven sylvia tanja thomas thorsten
tim timo tobias torsten udo ulf ulrich ulrike ursula ute uwe volker walter werner
wilhelm wolfgang yvonne
""".split())
#: Common German surnames: a name whose only distinctive word is one of these
#: is GENERIC (Braun GmbH, Lang GmbH).
SURNAMES = frozenset("""
mueller schmidt schneider fischer weber meyer meier wagner becker schulz hoffmann schaefer
koch bauer richter klein wolf schroeder neumann schwarz zimmermann braun krueger hofmann
hartmann lange lang schmitt werner schmitz krause meier lehmann schmid schulze maier
koehler herrmann koenig walter mayer huber kaiser fuchs peters scholz moeller weiss jung
hahn schubert vogel friedrich keller guenther frank berger winkler roth beck lorenz
baumann franke albrecht schuster simon ludwig boehm winter kraus martin schumacher kraemer
vogt stein jaeger otto sommer gross seidel heinrich brandt haas schreiber graf schulte
dietrich ziegler kuhn kuehn pohl engel horn busch bergmann thomas voigt sauer arnold wolff
pfeiffer
""".split())


#: Words a person's name never has ("Johannes Gutenberg-Universität Mainz").
_ORG_WORD_RE = re.compile(
    r"universit|hochschule|akademie|klinik|institut|zentrum|stiftung|apotheke|labor|gesellschaft|verein|"
    r"\b(?:gmbh|ag|kg|ev|e\.v\.)\b", re.IGNORECASE)


def _is_person(text: str) -> bool:
    """"Nicolai Thüns", "Dr. Anna Berger": a first name and a surname, no legal form."""
    words = _TITLE_RE.sub("", text.strip()).split()
    if not 2 <= len(words) <= 3 or legal_form_of(text) or _DEPARTMENT_RE.search(text) or _ORG_WORD_RE.search(text):
        return False
    return fold(words[0]) in FIRST_NAMES and all(w[:1].isupper() for w in words)


def clean(names) -> tuple[tuple[str | None, ...], tuple[str, ...]]:
    """``(Name 1–4 cleaned, contact parts removed)``. Name 1 is kept as it is."""
    names = [(" ".join(str(n).split()) if n else None) for n in (list(names) + [None] * 4)[:4]]
    out, contact = [names[0]], []
    for n in names[1:]:
        if not n:
            out.append(None)
            continue
        m = _CARE_OF_RE.search(n)
        if m:
            contact.append(n[m.start():].strip(" /"))
            n = n[:m.start()]
        n = n.strip(" /,;")
        if _NOTE_RE.match(n or ""):
            n = ""
        elif _is_person(n):
            contact.append(n)
            n = ""
        out.append(n or None)
    return tuple(out), tuple(contact)


# ── 1.2 Legal form ───────────────────────────────────────────────────────────
#: Spellings folded before :func:`vat.entity_match.parse_name` reads the form.
_FORM_SPELLINGS: tuple[tuple[re.Pattern, str], ...] = tuple((re.compile(a, re.IGNORECASE), b) for a, b in (
    (r"\bgemeinn(?:ü|ue)tzige\s+gesellschaft\s+mit\s+beschr(?:ä|ae)nkter\s+haftung\b", "gGmbH"),
    (r"\bpartnerschaftsgesellschaft\s+mbb\b|\bpartg\s?mbb\b", "PartGmbB"),
    (r"\bpartnerschaftsgesellschaft\b|\bpartg\b", "PartG"),
    (r"\beingetragene[rn]?\s+kauf(?:mann|frau)\b|\be\.\s?k(?:fm|fr)?\.?(?=\s|$)|\bek(?:fm)?\b(?=\s*$)", "eK"),
    (r"\bstiftung\s+(?:des\s+)?(?:(?:ö|oe)ffentlichen|b(?:ü|ue)rgerlichen)\s+rechts\b", "StiftungOeR"),
    (r"\banstalt\s+(?:des\s+)?(?:ö|oe)ffentlichen\s+rechts\b|\ba(?:ö|oe)r\b", "AdoeR"),
    (r"\bk(?:ö|oe)rperschaft\s+(?:des\s+)?(?:ö|oe)ffentlichen\s+rechts\b|\bkd(?:ö|oe)r\b", "KdoeR"),
    (r"\bkg\s?aa\b", "KGaA"),
    (r"\s*(?:\+|\band\b|\bund\b)\s*(?=co\b)", " & "),
))
#: Canonical forms beyond entity_match's (read from the folded spellings above).
_EXTRA_FORMS = {"partg": "partg", "partgmbb": "partgmbb", "ek": "ek", "stiftungoer": "stiftung",
                "adoer": "adoer", "kdoer": "kdoer", "ab": "ab", "stiftung": "stiftung"}
_STIFTUNG_RE = re.compile(r"(?:^|\s)stiftung(?=\s|$)|\w-stiftung$", re.IGNORECASE)
#: A form SAP's cut left unreadable: "… GmbH & Co", "… GmbH & C", "… Gmbh&".
_UNKNOWN_FAMILY_RE = re.compile(r"\bgmbh\s*&\s*(?:co?\.?)?\s*$|\bgmbh&$", re.IGNORECASE)


def _folded_forms(text: str) -> str:
    for rx, repl in _FORM_SPELLINGS:
        text = rx.sub(repl, text)
    return text


def legal_form_of(text: str | None, *, sap: bool = False) -> str | None:
    """The canonical legal form written in *text* (anywhere in it: "AG der
    Dillinger Hüttenwerke"), :data:`UNKNOWN_GMBH_FAMILY` for a form cut off at
    the 35-character limit, None for none."""
    raw = " ".join((text or "").split())
    if not raw:
        return None
    if _UNKNOWN_FAMILY_RE.search(raw):
        return UNKNOWN_GMBH_FAMILY
    folded = _folded_forms(raw)
    tokens = fold(folded).split()
    for t in tokens:
        if t in ("partg", "partgmbb", "ek", "stiftungoer", "adoer", "kdoer"):
            return _EXTRA_FORMS[t]
    form = parse_name(folded, sap=sap).form
    if form:
        return form
    if tokens and tokens[-1] == "ab" and len(tokens) > 1:
        return "ab"
    if _STIFTUNG_RE.search(raw):
        return "stiftung"
    return None


def forms_differ(a: str | None, b: str | None) -> bool:
    """Both state a form and they differ; UNKNOWN_GMBH_FAMILY differs from no
    GmbH-family form, and "no form" never differs."""
    if not a or not b or a == b:
        return False
    if UNKNOWN_GMBH_FAMILY in (a, b):
        other = b if a == UNKNOWN_GMBH_FAMILY else a
        return not other.startswith(("gmbh", "ggmbh", "ug"))
    return True


# ── 1.3 Name 2 classification ────────────────────────────────────────────────
_SUB_ENTITY_RE = re.compile(
    r"\b(?:universit(?:ä|ae)tsklinikum|universit(?:ä|ae)tsmedizin|klinikum|charit(?:é|e)|"
    r"medizinische\s+hochschule|studentenwerk|studierendenwerk|an-institut|f(?:ö|oe)rderverein)\b"
    r"|(?:^|\s)stiftung(?=\s|$)|\w-stiftung\b", re.IGNORECASE)
_NOT_SUB_RE = re.compile(r"klinik\s+f(?:ü|ue)r|vorklinik|stiftungs(?:professur|lehrstuhl|\b|$)", re.IGNORECASE)
_DEPARTMENT_RE = re.compile(
    r"\b(?:institut(?:e)?|inst\.?|lehrstuhl|fakult(?:ä|ae)t|fb|fachbereich|fachgebiet|fg|dez\.?|dezernat|"
    r"abt\.?|abteilung|klinik\s+f(?:ü|ue)r|zentrum\s+f(?:ü|ue)r|zentr\.|labor|einkauf|gb|beschaffung|"
    r"verwaltung|sekretariat|professur|stiftungsprofessur|stiftungs|prof\.?|arbeitsgruppe|ag\s+[a-zäöü]+|"
    r"bibliothek|rechenzentrum|werkfeuerwehr|accounting|business\s+unit|technology\s+cent(?:er|re)|"
    r"research\s+(?:&|and)\s+development|r\s?&\s?d|mn|"
    # Step AF: units, goods-in desks and sites that are never the legal entity.
    r"zentrum|centrum|center|rechnungseingang|wareneingang|retoure|kaserne|division|div\.?|"
    r"medizintechnik|logistik|lager|zentrale\s+dienste)(?=\W|$)", re.IGNORECASE)
#: Name 2 that names a site of the Name 1 entity: a BRANCH.
_SITE_FIELD_RE = re.compile(
    r"^(?:werk|standort|niederlassung|zweigniederlassung|betriebsst(?:ä|ae)tte|filiale)(?=\s|$)", re.IGNORECASE)
#: Connector words: a field that ends or starts with one is cut mid-name.
_CONNECTOR_RE = re.compile(r"\b(?:und|and|der|die|das|des|f(?:ü|ue)r|f\.|of|zu|im|am|an|&)$", re.IGNORECASE)
_LEADING_CONNECTOR_RE = re.compile(r"^(?:und|and|der|die|das|des|f(?:ü|ue)r|of|zu|im|am|&)\b", re.IGNORECASE)
#: Head nouns of an organisation's name: Name 2 supplies one Name 1 lacks
#: ("Chemisches und Veterinaer" / "Untersuchungsamt Sigmaringen").
_HEAD_NOUN_RE = re.compile(
    r"amt\b|anstalt\b|institut\b|inst\b|beh(?:ö|oe)rde\b|schule\b|akademie\b|universit|hochschule|"
    r"klinik|zentrum\b|gesellschaft\b|verband\b|verein\b|kammer\b|stiftung\b|ministerium\b|institute\b",
    re.IGNORECASE)
#: Name 1 is a department ("Institut für …") and Name 2–4 names its parent.
_DEPT_FIRST_RE = re.compile(
    r"^(?:institut|inst\.?|zentrum|labor|abteilung|lehrstuhl|klinik)\s+f(?:ü|ue)?r?\.?\b", re.IGNORECASE)
_PARENT_RE = re.compile(
    r"\b(?:\w+-)?(?:universit(?:ä|ae)t|hochschule|fachhochschule|klinikum|universit(?:ä|ae)tsklinikum)\b"
    r"|\buniversit(?:ä|ae)t\b", re.IGNORECASE)
_INSTITUTION_HEAD_RE = re.compile(r"universit|hochschule|klinikum|akademie", re.IGNORECASE)

# ── 1.4 Site words ───────────────────────────────────────────────────────────
_SITE_RE = re.compile(
    r"[\s,-]+(?:werk\s+(?=\S)|niederlassung\b|zweigniederlassung\b|standort\b|betriebsst(?:ä|ae)tte\b|"
    r"filiale\b|zentrallager\b).*$", re.IGNORECASE)
#: "… Zweigniederlassung der Bruker Daltonics GmbH & Co. KG": a branch of that entity.
_BRANCH_OF_RE = re.compile(r"\b(?:zweig)?niederlassung\s+(?:der|des)\s+(.+)$", re.IGNORECASE)
_TRAILING_FORM_RE = re.compile(
    r"\s((?:g?gmbh|mbh|ag|se|kg|kgaa|ohg|ug|e\.?\s?v\.?|eg)(?:\s*(?:&|\+)\s*co\.?\s*(?:kg|kgaa|ohg)?)?\.?)\s*$",
    re.IGNORECASE)

# ── 1.5 Distinctive tokens ───────────────────────────────────────────────────
GENERIC_WORDS = frozenset("""
deutschland germany international europe europa group gruppe holding service services technik
technologie technologies technology systems system solutions industrie industries labor
laboratory laboratories institut institute zentrum center centre chemie chemicals pharma medical
medizin consulting engineering trading handel vertrieb produktion werk werke gesellschaft
forschung research und der die das des fuer von zu am an im in the and of
""".split())


def distinctive_tokens(name: str | None) -> list[str]:
    return [t for t in parse_name(name).core if len(t) >= 4 and t not in GENERIC_WORDS]


def is_generic(name: str | None) -> bool:
    """No distinctive word, or its only distinctive words are common surnames."""
    tokens = distinctive_tokens(name)
    return not tokens or all(t in SURNAMES for t in tokens)


# ── Public bodies (2.1) ──────────────────────────────────────────────────────
_PUBLIC_RE = re.compile(
    r"\b(?:landes|landrats|landeskriminal|bundes|untersuchungs)?amt\b|\b\w*amt\b(?<!markt)|\bbundes\w+|"
    r"\bpolizei\w*|\bstadt\b|\bgemeinde\b|\blandkreis\b|\bministerium\b|\bkommission\b|\bbeh(?:ö|oe)rde\b|"
    r"(?<!hoch)(?<!fachhoch)schule\b|\bgymnasium\b|untersuchungsamt", re.IGNORECASE)


def is_public_body(*names: str | None) -> bool:
    return bool(_PUBLIC_RE.search(" ".join(n for n in names if n)))


@dataclass(frozen=True)
class NamePlan:
    """How one record is searched and matched."""

    cls: str                                   # SUB_ENTITY / CONTINUATION / DEPARTMENT / OTHER
    entity: str                                # the legal entity's name
    search_name: str                           # name for search 1 (before quoting)
    acceptable: tuple[str, ...]                # names a page may show for the entity
    names: tuple[str | None, ...]              # Name 1–4 cleaned
    contact: tuple[str, ...] = ()
    parent: str | None = None                  # Name 1 for a SUB_ENTITY; the parent in reverse order
    form: str | None = None
    issues: tuple[str, ...] = ()               # SUB_ENTITY / DEPARTMENT / BRANCH
    generic: bool = False
    public: bool = False
    complete: bool = True                      # not cut at SAP's 35 characters
    reverse: bool = False
    context: tuple[str, ...] = field(default_factory=tuple)  # Name 2–4 given to the model only

    @property
    def match_names(self) -> tuple[str | None, ...]:
        """Name 1–4 for the matcher: the entity first."""
        return tuple([self.entity, *self.acceptable[1:2]] + [None] * 4)[:4]


def _join(a: str, b: str) -> str:
    """A word cut by a hyphen is mended ("Veterinärunter-" + "suchungsamt");
    a compound's hyphen stays ("Gesundheits-" + "und")."""
    if a.endswith("-"):
        return a[:-1] + b if b[:1].islower() and not re.match(r"(?:und|oder)\b", b) else a + b
    return f"{a} {b}"


_FORM_PHRASE_END_RE = re.compile(
    r"(?:stiftung|anstalt|k(?:ö|oe)rperschaft)\s+(?:des\s+)?\w+\s+rechts\s*$", re.IGNORECASE)


def _until_last_form(text: str) -> str:
    """*text* cut after its last legal-form word ("ThyssenKrupp Industrial
    Solutions AG Business Unit Process" → "… Solutions AG"); a form written
    out in words ("Stiftung öffentlichen Rechts") is kept whole."""
    if _FORM_PHRASE_END_RE.search(text):
        return text
    words = text.split()
    for i in range(len(words), 0, -1):
        head = " ".join(words[:i])
        tail = words[i - 1]
        if legal_form_of(tail) or (i >= 2 and legal_form_of(" ".join(words[i - 2:i])) and
                                   not legal_form_of(" ".join(words[:i - 1]))):
            return head
    return text


def _form_completion(name_2: str) -> bool:
    """"& Co. KG", "+ Co KG", "GmbH", "Stiftung öffentlichen Rechts":
    nothing but a legal form."""
    core = parse_name(_folded_forms(name_2)).core
    folded = fold(_folded_forms(name_2)).split()
    return bool(re.match(r"^\s*(?:&|\+|und\b|u\.)\s*co\b", name_2, re.IGNORECASE)) or (
        bool(legal_form_of(name_2)) and (not core or core == tuple(folded)) and all(
            t in ("gmbh", "ggmbh", "ag", "kg", "co", "se", "ev", "eg", "ohg", "kgaa", "ug", "mbh", "partg",
                  "partgmbb", "ek", "stiftungoer", "adoer", "kdoer", "gbr", "bv", "nv", "ltd", "inc")
            for t in folded))


def _form_at_end(text: str) -> bool:
    """The field ends with a legal form ("Deutschland GmbH & Co KG"), not a
    code that contains one ("Abt AT-AN-SE-AS")."""
    return bool(_TRAILING_FORM_RE.search(" " + text)) or bool(re.search(
        r"\b(?:b\.?\s?v|n\.?\s?v|ltd|inc|s\.?\s?a|s\.?r\.?l|ab|partg(?:mbb)?|e\.?\s?k)\.?\s*$", text, re.IGNORECASE))


def _parent_token(name: str) -> str | None:
    """The parent's word added to a sub-entity's search: its acronym if it
    has one ("TUM"), else its first distinctive word."""
    acronym = next((w for w in name.split() if re.fullmatch(r"[A-ZÄÖÜ]{2,6}", w)), None)
    if acronym:
        return acronym
    words = [w for w in re.split(r"[\s-]+", name) if w[:1].isupper() and fold(w) not in GENERIC_WORDS]
    if 2 <= len(words) <= 5 and not legal_form_of(name):
        return "".join(w[0] for w in words).upper()  # "Technische Universität München" → TUM
    tokens = [t for t in distinctive_tokens(name) if t not in ("technische", "universitaet", "hochschule")]
    return tokens[0] if tokens else None


def _cut(name_1: str) -> bool:
    return len(name_1.strip()) >= SAP_NAME_LIMIT - 1


def prepare(names, *, city: str | None = None) -> NamePlan:
    """The :class:`NamePlan` for Name 1–4 (raw, as in SAP)."""
    cleaned, contact = clean(names)
    n1 = cleaned[0] or ""
    rest = [n for n in cleaned[1:] if n]
    issues: list[str] = []

    # A person in Name 1 with the entity after it ("Andreas Gubernator" /
    # "Bruker Nano GmbH"): the person is a contact.
    if rest and _is_person(n1):
        contact = (n1, *contact)
        n1, rest = rest[0], rest[1:]
        cleaned = (n1, *rest, *([None] * (3 - len(rest))))[:4]

    # 1.4 site words in Name 1 (and "Zweigniederlassung der X" in Name 1–2).
    branch_of = _BRANCH_OF_RE.search(" ".join([n1, *rest[:1]]))
    if branch_of:
        issues.append(BRANCH)
        target = branch_of.group(1).strip(" -,")
        return _plan(CONTINUATION if legal_form_of(target) else OTHER, target, target, (target,), cleaned,
                     contact, issues, context=tuple(rest[1:]))
    stripped = _SITE_RE.sub("", n1).strip(" -,/") if _SITE_RE.search(n1) else ""
    if stripped:
        issues.append(BRANCH)
        form = _TRAILING_FORM_RE.search(n1)
        n1_entity = f"{stripped} {form.group(1)}" if form and not legal_form_of(stripped) else stripped
    else:
        n1_entity = n1

    # Reverse order: "Institut f. Werkstoffwissenschaften" / "Martin-Luther-Universität".
    if _DEPT_FIRST_RE.match(n1) and rest:
        parent = next((r for r in rest if _PARENT_RE.search(r)), None)
        if parent:
            return _plan(DEPARTMENT, parent, parent, (parent,), cleaned, contact, issues + [DEPARTMENT],
                         parent=parent, reverse=True, context=(n1, *[r for r in rest if r != parent]))
    # A department inside Name 1: "Universität Stuttgart Institut für Werkstoffkunde".
    m = _DEPARTMENT_RE.search(n1)
    if m and m.start() > 0 and _INSTITUTION_HEAD_RE.search(n1[:m.start()]):
        head = n1[:m.start()].strip(" ,-/")
        return _plan(DEPARTMENT, head, head, (head,), cleaned, contact, issues + [DEPARTMENT],
                     context=(n1[m.start():], *rest))

    n1_form = legal_form_of(n1_entity, sap=True)
    n1_own_form = legal_form_of(n1_entity)  # written, not read back from a cut ("… Gesellschaft")
    n1_kinds = {fold(m.group(0)) for m in _SUB_ENTITY_RE.finditer(n1_entity)}
    # SUB_ENTITY: its own legal entity under the Name 1 umbrella.
    sub_field = None
    for r in rest:
        if _form_completion(r):
            continue
        word = _SUB_ENTITY_RE.search(r)
        if word and not _NOT_SUB_RE.search(r) and fold(word.group(0)) not in n1_kinds:
            sub_field = r
            break
        if n1_own_form and _form_at_end(r):
            sub_field = r
            break
    if sub_field is not None:
        sub = _until_last_form(sub_field) if legal_form_of(sub_field) else sub_field
        if not distinctive_tokens(sub):
            # "Deutschland GmbH & Co KG" under "Keysight Technologies GmbH".
            base = " ".join(w for w in n1_entity.split() if not legal_form_of(w))
            sub = _join(base, sub)
        parent_token = _parent_token(n1_entity)
        search = sub if not parent_token or fold(parent_token) in fold(sub) else f"{sub} {parent_token}"
        return _plan(SUB_ENTITY, sub, search, (sub,), cleaned, contact, issues + [SUB_ENTITY],
                     parent=n1_entity, context=tuple(r for r in rest if r != sub_field))

    # CONTINUATION: Name 2 (or 3) carries the legal form Name 1 lacks, or completes it.
    for i, r in enumerate(rest[:2]):
        if (legal_form_of(r) and not n1_own_form) or (i == 0 and _form_completion(r)):
            parts = rest[:i + 1]
            joined = n1_entity
            for p in parts:
                joined = _join(joined, p)
            joined = _until_last_form(joined)
            return _plan(CONTINUATION, joined, joined, (joined, n1_entity), cleaned, contact, issues,
                         context=tuple(rest[i + 1:]), complete=True)

    n2 = rest[0] if rest else None
    # Name 2 names a site of the Name 1 entity: "K+S … GmbH" / "Werk Bernburg".
    if n2 and _SITE_FIELD_RE.match(n2):
        return _plan(OTHER, n1_entity, n1_entity, (n1_entity,), cleaned, contact, issues + [BRANCH],
                     context=tuple(rest), complete=not _cut(n1))
    # Rule 10, a visible cut: Name 1 or Name 2 breaks off mid-name ("NMI
    # Naturwissenschaftliches und" / "und Medizinisches Institut an der"), or
    # Name 2 supplies the head noun Name 1 lacks ("Chemisches und Veterinaer"
    # / "Untersuchungsamt Sigmaringen"). The fields are one name, so a
    # department word inside them does not make it a department.
    if n2 and not legal_form_of(n2) and _visibly_cut(n1_entity, n2):
        return _truncated_plan(n1_entity, rest, cleaned, contact, issues)
    # DEPARTMENT: a department word anywhere in Name 2–4.
    if rest and any(_DEPARTMENT_RE.search(r) for r in rest):
        return _plan(DEPARTMENT, n1_entity, n1_entity, (n1_entity,), cleaned, contact, issues + [DEPARTMENT],
                     context=tuple(rest), complete=not _cut(n1))
    # Rule 10: Name 1 at SAP's limit and Name 2 neither a department, a
    # person, a c/o nor a legal form: Name 2 carries on the name.
    if n2 and _cut(n1) and not legal_form_of(n2):
        # Cut at SAP's limit, maybe mid-word ("… Wissenschaftlic" / "Services"):
        # the joined name is not complete, so it is never quoted.
        return _truncated_plan(n1_entity, rest[:1], cleaned, contact, issues, complete=False)
    return _plan(OTHER, n1_entity, n1_entity, (n1_entity,), cleaned, contact, issues,
                 context=tuple(rest), complete=not _cut(n1))


def _visibly_cut(n1: str, n2: str) -> bool:
    if n1.endswith("-") or _CONNECTOR_RE.search(n1) or _LEADING_CONNECTOR_RE.match(n2):
        return True
    return (not legal_form_of(n1) and not _HEAD_NOUN_RE.search(n1) and bool(_HEAD_NOUN_RE.search(n2))
            and not _DEPARTMENT_RE.search(n2))


def join_truncated(a: str, b: str) -> str:
    """*a* and *b* as one name; a leading word of *b* that repeats the end of
    *a* is dropped ("… und" + "und Medizinisches …")."""
    words = b.split()
    if words and a.split() and fold(words[0]) == fold(a.split()[-1]):
        b = " ".join(words[1:])
    return _join(a, b) if b else a


def _truncated_plan(n1: str, rest: list[str], cleaned, contact, issues, *, complete: bool = True) -> NamePlan:
    """CONTINUATION_TRUNCATED: Name 1 joined with the fields that carry it on
    (while a field ends on a connector, the next one too, and a trailing
    legal form); acceptable names: the joined name and Name 1 alone."""
    joined, used = join_truncated(n1, rest[0]), 1
    while used < len(rest) and (joined.endswith("-") or _CONNECTOR_RE.search(joined)) \
            and not _DEPARTMENT_RE.search(rest[used]) and not legal_form_of(rest[used]):
        joined, used = join_truncated(joined, rest[used]), used + 1
    if used < len(rest) and _form_completion(rest[used]):
        joined, used = _join(joined, rest[used]), used + 1
    return _plan(CONTINUATION_TRUNCATED, joined, joined, (joined, n1), cleaned, contact, issues,
                 context=tuple(rest[used:]), complete=complete)


def _plan(cls, entity, search, acceptable, cleaned, contact, issues, *, parent=None, reverse=False,
          context=(), complete=True) -> NamePlan:
    form = legal_form_of(entity, sap=not complete)
    if form is None and not complete:
        form = None
    if legal_form_of(entity) == UNKNOWN_GMBH_FAMILY:
        form, complete = UNKNOWN_GMBH_FAMILY, False
    generic = is_generic(entity)
    if generic:
        issues = [*issues, GENERIC_NAME]
    return NamePlan(cls=cls, entity=entity, search_name=search, acceptable=tuple(dict.fromkeys(acceptable)),
                    names=cleaned, contact=contact, parent=parent, form=form,
                    issues=tuple(dict.fromkeys(issues)), generic=generic,
                    public=is_public_body(entity, *(cleaned[1:] if cls != SUB_ENTITY else ())),
                    complete=complete, reverse=reverse, context=tuple(c for c in context if c))
