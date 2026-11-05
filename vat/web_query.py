"""The llm_web "hybrid" search (``vat/lookup_web.py``): the first query built
by code, and the parts of a fetched page the reader is shown.

* :func:`first_query` — search 1, no model choice:
  ``<name for search> <city for search> Impressum OR Imprint OR "Legal notice"``,
  plus ``USt-IdNr`` for universities, hospitals, research institutes and
  public bodies (they publish it on procurement pages, not in the Impressum).
* :func:`page_sections` — the head of the page and ±window characters around
  every USt-IdNr / Umsatzsteuer-Identifikationsnummer / VAT / Impressum /
  "Angaben gemäß" mention, instead of the first N characters.
* :func:`no_vat_statement` — the page says the entity has no USt-IdNr.
"""

from __future__ import annotations

import re

from vat.entity_match import DEPARTMENT_WORDS, SAP_NAME_LIMIT, SITE_WORDS, fold, has_department, parse_name

#: Appended to search 1 for an institution.
INSTITUTION_TERM = "USt-IdNr"
IMPRESSUM_TERMS = 'Impressum OR Imprint OR "Legal notice"'

_CARE_OF_RE = re.compile(r"\s*(?:\bc/o\b|\bz\.\s?Hd\.?).*$", re.IGNORECASE)
#: A standalone "+", "u." or "und" between two words → "&".
_AND_RE = re.compile(r"(?<=\s)(?:\+|u\.|und)(?=\s)", re.IGNORECASE)
_WORD_RE = re.compile(r"\w", re.UNICODE)

_INSTITUTION_RE = re.compile(
    r"universit|hochschule|akademie|klinik|krankenhaus|spital|\binst\b|institut|"
    r"forschung|max planck|fraunhofer|helmholtz|leibniz|schule|gymnasium|"
    r"behoerde|ministerium|\bamt\b|amt\b|landkreis|gemeinde|stadtverwaltung|"
    r"\bkdoer\b|\badoer\b|bundesanstalt|landesanstalt",
)
#: Public bodies that normally have no USt-IdNr: schools and authorities.
#: Universities and hospitals are not in it — they usually have one.
_PUBLIC_BODY_RE = re.compile(
    r"(?<!hoch)(?<!fachhoch)schule\b|\bschule|gymnasium|behoerde|ministerium|\bamt\b|"
    r"(?:landrats|finanz|bau|ordnungs|gesundheits|jugend|bundes|landes)amt\b|"
    r"stadtverwaltung|gemeindeverwaltung|kreisverwaltung|regierungspraesidium|polizei",
)

#: Glued river / region qualifiers SAP writes into the city ("HATTERSHEIMAMMAIN").
_GLUED_QUALIFIER_RE = re.compile(
    r"(?<=[a-zäöüß]{3})(?:am ?main|an der ?donau|am ?neckar|am ?rhein|im ?taunus|"
    r"in der ?pfalz|an der ?saale|an der ?ruhr)$",
    re.IGNORECASE,
)
_RIVERS = frozenset({"saale", "main", "oder", "donau", "neckar", "rhein", "ruhr", "lahn", "elbe", "weser", "mosel",
                     "havel", "spree", "isar", "inn", "fulda", "werra", "ems", "lech", "iller"})
#: First words of two-word towns that stay apart ("Bad Kreuznach", "Sankt Augustin").
_TOWN_PREFIXES = frozenset({"bad", "sankt", "st", "neu", "alt", "gross", "klein", "ober", "unter", "nieder",
                            "hohen", "markt", "stadt", "schloss", "kloster", "lutherstadt", "hansestadt",
                            "ostseebad", "nordseebad", "seebad"})
_LOWER_WORDS = frozenset({"am", "an", "im", "in", "der", "die", "den", "ob", "bei", "vor", "auf", "a", "i"})
#: Towns SAP writes without umlauts (the folded key → the usual spelling).
#: Only towns where the plain spelling is another place or misleads a search.
_UMLAUT_TOWNS = {
    "munster": "Münster", "munchen": "München", "koln": "Köln", "dusseldorf": "Düsseldorf",
    "nurnberg": "Nürnberg", "wurzburg": "Würzburg", "tubingen": "Tübingen",
    "gottingen": "Göttingen", "saarbrucken": "Saarbrücken", "lubeck": "Lübeck",
    "osnabruck": "Osnabrück", "furth": "Fürth", "lorrach": "Lörrach", "boblingen": "Böblingen",
    "goppingen": "Göppingen", "monchengladbach": "Mönchengladbach", "julich": "Jülich",
    "giessen": "Gießen", "luneburg": "Lüneburg", "dortmund": "Dortmund",
}


def _words(text: str) -> list[str]:
    return [t for t in text.split() if _WORD_RE.search(t)]


def _cut_at(tokens: list[str], phrases, *, min_left: int) -> list[str]:
    """*tokens* before the first of *phrases* (folded) that starts after the
    first token, when at least *min_left* word tokens remain; else *tokens*."""
    folded = [fold(t) for t in tokens]
    for i in range(1, len(tokens)):
        for p in phrases:
            if tuple(folded[i:i + len(p)]) == p:
                head = tokens[:i]
                return head if len(_words(" ".join(head))) >= min_left else tokens
    return tokens


#: Words a name does not end on: what follows went to Name 2.
_DANGLING = frozenset({"fuer", "des", "der", "die", "und", "von", "zu", "of", "for", "and"})


def at_name_limit(name_1: str | None) -> bool:
    """Name 1 is as long as SAP keeps, or ends mid-word ("Baustoff-") or on
    "für" / "des" …: it continues in Name 2 or was cut."""
    name = (name_1 or "").strip()
    last = name.split()[-1] if name.split() else ""
    return len(name) >= SAP_NAME_LIMIT - 1 or last.endswith("-") or fold(last) in _DANGLING


def search_name(name_1: str | None) -> str:
    """Name 1 as a search term: "c/o …" / "z. Hd. …" and everything after a
    site word (Werk, Niederlassung, Standort …) removed, a department word
    and what follows removed when ≥ 2 words remain, a fragment SAP's
    35-character cut left at the end dropped, and a standalone "+" / "u." /
    "und" written "&"."""
    name = _CARE_OF_RE.sub("", (name_1 or "").strip())
    name = _AND_RE.sub("&", name)
    tokens = name.split()
    tokens = _cut_at(tokens, [(w,) for w in SITE_WORDS], min_left=1)
    tokens = _cut_at(tokens, DEPARTMENT_WORDS, min_left=2)
    if at_name_limit(name_1) and len(tokens) > 1:
        last = tokens[-1]
        cut_form = parse_name(" ".join(tokens), sap=True).core != parse_name(" ".join(tokens)).core
        if last.endswith("-") or len(fold(last)) <= 3 or cut_form:
            tokens = tokens[:-1]
    while tokens and (not _WORD_RE.search(tokens[-1]) or tokens[-1].endswith("-")
                      or fold(tokens[-1]) in _DANGLING):  # "-", "&", "Baustoff-", "für"
        tokens = tokens[:-1]
    return " ".join(tokens)


def search_city(city: str | None) -> str:
    """The city in its usual spelling: title case with "am", "an der" … in
    lower case, a qualifier glued on by SAP dropped ("HATTERSHEIMAMMAIN" →
    "Hattersheim"), and a few towns SAP writes without umlauts restored
    ("MUNSTER" → "Münster")."""
    text = " ".join((city or "").split())
    if not text:
        return ""
    if " " not in text:
        text = _GLUED_QUALIFIER_RE.sub("", text)
    words = text.split()
    # "ROSBACH V.D.H.", "FRANKFURT A. M.": a dotted qualifier after the town.
    while len(words) > 1 and "." in words[-1]:
        words = words[:-1]
    # "HALLE SAALE": the river after the town.
    if len(words) == 2 and fold(words[1]) in _RIVERS:
        words = words[:1]
    # "REHLINGEN SIERSBURG": two town names written apart are one.
    if len(words) == 2 and all(len(w) >= 5 for w in words) and \
            fold(words[0]) not in _TOWN_PREFIXES and fold(words[1]) not in _LOWER_WORDS:
        words = [f"{words[0]}-{words[1]}"]
    text = " ".join(words)
    key = fold(text, umlaut_e=False).replace(" ", "")
    known = _UMLAUT_TOWNS.get(key) or _UMLAUT_TOWNS.get(
        key.replace("ae", "a").replace("oe", "o").replace("ue", "u"))  # "BOEBLINGEN"
    if known:
        return known
    out = []
    for i, word in enumerate(text.split()):
        low = word.lower()
        if i and low.rstrip(".") in _LOWER_WORDS:
            out.append(low)
        else:
            out.append("-".join(p[:1].upper() + p[1:].lower() for p in word.split("-")))
    return " ".join(out)


def is_institution(*names: str | None) -> bool:
    """A university, hospital, research institute or public body."""
    return bool(_INSTITUTION_RE.search(fold(" ".join(n for n in names if n))))


def is_public_body(*names: str | None) -> bool:
    """A school or an authority: normally no USt-IdNr."""
    return bool(_PUBLIC_BODY_RE.search(fold(" ".join(n for n in names if n))))


_FORM_START_RE = re.compile(r"^\s*(?:&|\+|und\b|u\.)", re.IGNORECASE)
_NOT_A_NAME_RE = re.compile(r"^\s*(?:>>|\d|inst\b|fb\b|abt\b|lehrstuhl|fakult|fachbereich|z\.\s?hd|c/o)",
                            re.IGNORECASE)


def _forms_of(text: str) -> bool:
    return parse_name(text).form is not None


def query_name(name_1: str | None, name_2: str | None = None) -> tuple[str, bool, bool]:
    """``(name for search, complete, Name 2 appended)``. Name 2 is appended
    when it carries the legal form ("& Co. KG", "GmbH", "Deutschland GmbH &
    Co KG") or continues a Name 1 that was cut ("IBQ - Institut für
    Baustoff-" + "Qualitätssicherung GmbH"), never when it is a department,
    a brand or a note. When both state a legal form, Name 1's goes: "Keysight
    Technologies GmbH" + "Deutschland GmbH & Co KG" → "Keysight Technologies
    Deutschland GmbH & Co KG". Words after the last legal form are dropped."""
    n1 = " ".join((name_1 or "").split())
    n2 = " ".join((name_2 or "").split())
    cut = at_name_limit(n1)
    carries = bool(n2) and (_FORM_START_RE.match(n2) is not None or _forms_of(n2))
    # Unambiguously cut: mid-word ("Baustoff-") or on "für" / "des". A name
    # that only reaches 35 characters may be complete ("Karlsruher Institut
    # für Technologie"); its Name 2 is appended only when it carries the form.
    last = n1.split()[-1] if n1.split() else ""
    open_end = n1.endswith("-") or fold(last) in _DANGLING
    continues = bool(n2) and open_end and not has_department(n2)
    if not n2 or _NOT_A_NAME_RE.match(n2) or not (carries or continues):
        name = search_name(n1)
        return name, bool(name) and not cut, False
    if n1.endswith("-"):
        joined = n1 + n2
    elif _FORM_START_RE.match(n2):
        joined = f"{n1} {n2}"
    else:
        tokens = n1.split()
        if carries and _forms_of(n2) and tokens and fold(tokens[-1]) in _FORM_TOKENS and len(n2.split()) > 1:
            tokens = tokens[:-1]
        joined = " ".join([*tokens, n2])
    words = joined.split()
    last_form = max((i for i, w in enumerate(words) if fold(w).replace(" ", "") in _FORM_TOKENS), default=None)
    if last_form is not None:
        words = words[:last_form + 1]
    name = _AND_RE.sub("&", _CARE_OF_RE.sub("", " ".join(words)))
    # Cut at the length limit mid-word ("… Wissenschaftlic" + "Services") stays incomplete.
    complete = not (len(n1) >= SAP_NAME_LIMIT - 1 and not n1.endswith("-") and not _forms_of(n1)
                    and not _FORM_START_RE.match(n2))
    return name, complete, True


#: Single-token legal forms as written (folded, without dots and spaces).
_FORM_TOKENS = frozenset({"gmbh", "ggmbh", "ag", "kg", "kgaa", "ohg", "se", "ug", "ev", "eg", "gbr",
                          "mbh", "co", "cokg", "kg.", "ltd", "inc", "llc"})


def query_for(plan, city: str | None) -> str:
    """Search 1 from a :class:`vat.name_prep.NamePlan`: ``<search name>
    <city>``. The name is quoted only when complete (not cut at SAP's 35
    characters, no fragment form) and at least two words; a GENERIC name is
    never quoted (its legal form is in it, unquoted). The city is never
    quoted. A public body gets ``USt-IdNr``."""
    from vat.name_prep import UNKNOWN_GMBH_FAMILY

    name = _AND_RE.sub("&", " ".join(plan.search_name.split())).strip(" -,/")
    if name and plan.complete and plan.form != UNKNOWN_GMBH_FAMILY and not plan.generic \
            and len(_words(name)) >= 2:
        name = f'"{name}"'
    parts = [name, search_city(city)]
    if plan.public:
        parts.append(INSTITUTION_TERM)
    return " ".join(p for p in parts if p)


def first_query(name_1: str | None, city: str | None, name_2: str | None = None,
                name_3: str | None = None, name_4: str | None = None) -> str:
    """Search 1 for Name 1–4 (see :func:`query_for`)."""
    from vat.name_prep import prepare

    return query_for(prepare((name_1, name_2, name_3, name_4)), city)


# ---------------------------------------------------------------------------
# Page sections
# ---------------------------------------------------------------------------

#: Mentions that carry the number, first; then where a legal notice starts.
_VAT_MENTION_RE = re.compile(
    r"ust[\s.-]*id[\s.-]*nr|umsatzsteuer[\s-]*identifikations?[\s-]*n(?:umme)?r|\bvat\b|\buid\b",
    re.IGNORECASE,
)
_NOTICE_RE = re.compile(r"impressum|imprint|angaben\s+gem(?:ä|ae)(?:ß|ss)", re.IGNORECASE)
PAGE_HEAD_CHARS = 1500


def page_sections(text: str, *, window: int = 1500, limit: int = 12_000) -> str:
    """The page head and ±*window* characters around every VAT / Impressum
    mention, merged and in page order, at most *limit* characters. When they
    do not all fit, the VAT mentions are kept first, then the Impressum
    mentions, then the head."""
    text = text or ""
    if len(text) <= limit:
        return text
    spans: list[tuple[int, int]] = []
    for rx in (_VAT_MENTION_RE, _NOTICE_RE):
        spans += [(max(0, m.start() - window), min(len(text), m.end() + window)) for m in rx.finditer(text)]
    spans.append((0, min(len(text), PAGE_HEAD_CHARS)))
    kept: list[list[int]] = []
    used = 0
    for start, end in spans:
        for s, e in kept:  # drop what is already kept
            if start < e and end > s:
                if start >= s:
                    start = e
                elif end <= e:
                    end = s
        if end <= start:
            continue
        end = min(end, start + (limit - used))
        kept.append([start, end])
        used += end - start
        if used >= limit:
            break
    merged: list[list[int]] = []
    for s, e in sorted(kept):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return " … ".join(text[s:e] for s, e in merged)


_NO_VAT_RE = re.compile(
    r"keine\s+(?:umsatzsteuer[\s-]*identifikations?[\s-]*nummer|ust[\s.-]*id[\s.-]*nr)"
    r"|(?:verf(?:ü|ue)g\w*|besitz\w*|hab\w*)\s+(?:\w+\s+){0,3}?(?:keine|nicht\s+über\s+eine)\s+"
    r"(?:umsatzsteuer[\s-]*identifikations?[\s-]*nummer|ust[\s.-]*id)"
    r"|nicht\s+umsatzsteuerpflichtig"
    r"|(?:als|sind|ist|bin)\s+(?:ein\s+)?kleinunternehmer|kleinunternehmerregelung\s+(?:nach|gem)"
    r"|gem(?:ä|ae)(?:ß|ss)\s*§\s*19\s*(?:abs\.?\s*1\s*)?ustg"
    r"|no\s+vat\s+(?:identification\s+|registration\s+)?number",
    re.IGNORECASE,
)


def no_vat_statement(text: str | None, window: int = 120) -> str | None:
    """The passage in which the page says the entity has no USt-IdNr. (none,
    not liable to VAT, Kleinunternehmer / § 19 UStG), or None."""
    m = _NO_VAT_RE.search(text or "")
    if not m:
        return None
    return " ".join(text[max(0, m.start() - window):m.end() + window].split())
