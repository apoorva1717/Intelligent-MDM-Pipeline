"""Record classification — the single authority for ``record_type``.

``record_type`` used to be written by whichever tier happened to run last: ROR
org types, then an LEI hit, then company canonicalisation, each overwriting the
one before. That produced MIT classified ``company`` because it holds an LEI,
a hospital classified ``company`` by the company branch, and ``unknown`` on 21
of 50 demo records without anything ever having decided so.

Two values are needed, and they are not the same value:

``routing_type``
    Provisional. Written early and updated as tiers report, exactly as before
    this module existed. It gates behaviour *during* the run — which Tier 1
    branch to take, whether Tier 2A/2B/lab resolution may run. Internal only:
    never serialised, never in the response. The pipeline needs a type before
    the evidence that decides the final one exists, so this cannot simply be
    deferred to the end.

``record_type``
    Final. Computed once, here, from ranked evidence, at the end of
    ``finalise``. The only value that reaches the output, the Excel export and
    Phase 2 dedup.

Evidence ranking — first source that yields an answer wins:

1. **ROR organisation types.** The existing mapping, unchanged (education,
   healthcare, government, facility, nonprofit, archive, other →
   ``research_institution``; company → ``company``).
2. **GLEIF entity metadata** — ``entity.category`` and ``entity.legalForm.id``
   from the ``lei-records`` response the pipeline already fetches. See
   :mod:`enrichment.elf_codes`.
3. **Corporate legal-form suffix** —
   :func:`utils.text_utils.has_corporate_legal_suffix`. It can only ever yield
   ``company``, and it is the mirror of (4): a legal form is the entity's
   registered commercial character, stated in its own name. Read off the input
   and verified against nothing, so it ranks below both registries and can
   never override one.
4. **Keyword heuristic** — :func:`utils.text_utils.looks_like_research_institution`.
   It can only ever yield ``research_institution``: the name not looking like an
   institution is not evidence of a company.
5. **``unknown``** — no source produced an answer.

(3) sits above (4) rather than below it because the one name in 200 that carries
both signals — ``Bio-Rad Laboratory Inc`` — is a company, and because a legal
form is a statement about the entity where a keyword is a statement about a
word. The two disagree rarely; when they do, this is the right way round.

An ambiguous or absent field yields *nothing* and falls through, rather than
guessing. ``unknown`` is a real terminal state meaning "no tier resolved the
type with confidence" — deliberately preferred over defaulting to ``company``,
which would assert something the pipeline does not know.

Tier 3 contributes no evidence here and never has any: it is a last-resort name
guesser with no classification signal.
"""

from __future__ import annotations

from dataclasses import dataclass

from enrichment.elf_codes import COMMERCIAL_ELF, NON_COMMERCIAL_ELF
from utils.text_utils import (
    has_corporate_legal_suffix,
    looks_like_research_institution,
)

RESEARCH = "research_institution"
COMPANY = "company"
#: A public body. ROR reports it as an org type and GLEIF as an entity
#: category; both used to be folded into ``research_institution``, so a
#: registry that had correctly identified a government agency had its answer
#: discarded on the way out. Measured on the 200 labelled records: 55 of the
#: 61 S3 errors were exactly that, 50 of them decided by ``ror:verified``.
GOVERNMENT = "government"
UNKNOWN = "unknown"

# Sources reported in `record_type_source`.
SOURCE_ROR = "ror"
SOURCE_GLEIF = "gleif"
SOURCE_LEGAL_FORM = "legal_form"
SOURCE_KEYWORD = "keyword"
SOURCE_UNRESOLVED = "unresolved"

# ``entity.category`` values that carry a signal. Sampled live: GENERAL is the
# overwhelming majority and is what BOTH MIT and Pfizer carry, so it says
# nothing at all and is absent here. SOLE_PROPRIETOR / FUND / BRANCH are
# trading entities; the two public-body categories are not, and map the same way
# ROR's own "government" org type already does.
_CATEGORY_TYPE: dict[str, str] = {
    "SOLE_PROPRIETOR": COMPANY,
    "FUND": COMPANY,
    "BRANCH": COMPANY,
    # Both were RESEARCH until `government` existed to receive them. The
    # comment below still holds: they map the way ROR's own "government" org
    # type maps — which is now to GOVERNMENT, on both sides.
    "RESIDENT_GOVERNMENT_ENTITY": GOVERNMENT,
    "INTERNATIONAL_ORGANIZATION": GOVERNMENT,
}

# ``legalForm.other`` free text, used only when the code is a catch-all (8888
# "other" / 9999 "not on the list") and therefore carries no meaning itself.
# MIT is 8888 with other="INSTITUTE"; Brigham and Women's is 8888 with
# other="Hospital"; Pfizer Canada is 8888 with other="INCORPORATED / INCORPOREE".
_OTHER_NON_COMMERCIAL = (
    "institute", "institut", "university", "universit", "college", "school",
    "hospital", "clinic", "foundation", "stiftung", "academy", "akademie",
    "non-profit", "nonprofit", "not-for-profit", "charity", "charitable",
    "research",
)
_OTHER_COMMERCIAL = (
    "incorporated", "incorporee", "corporation", "company", "limited",
    "partnership", "gmbh", "aktiengesellschaft", "proprietary",
)


@dataclass(frozen=True)
class TypeEvidence:
    """What the record carries by the time ``finalise`` classifies it.

    Parameters
    ----------
    name1
        The enriched organisation name — the corrected one where a tier or the
        Tier 1 re-lookup produced it, since that is what the keyword heuristic
        should judge.
    ror_is_research
        ``True``/``False`` when ROR matched (already mapped from its org types
        by the ROR client), ``None`` when ROR never matched.
    ror_org_types
        The org types ROR actually published, unflattened. Only ``government``
        is read from them; the boolean above still decides everything else.
    lei_id
        Present when GLEIF verified a match. On its own it is NOT evidence of a
        company — see :func:`_from_gleif`.
    gleif_category, gleif_legal_form_id, gleif_legal_form_other
        ``entity.category``, ``entity.legalForm.id`` and ``entity.legalForm.other``
        from that GLEIF record.
    """
    name1: str | None = None
    ror_is_research: bool | None = None
    ror_org_types: "tuple[str, ...] | None" = None
    lei_id: str | None = None
    gleif_category: str | None = None
    gleif_legal_form_id: str | None = None
    gleif_legal_form_other: str | None = None


#: ROR org types that mean a public body rather than a research organisation.
#: ``government`` is ROR's own word; ``archive`` is deliberately NOT here — a
#: national archive is a collection, and the label set treats it as research.
_ROR_GOVERNMENT_TYPES = frozenset({"government"})


def _from_ror(ev: TypeEvidence) -> str | None:
    """ROR's verdict, no longer flattened.

    ``ror_is_research`` is a boolean built from seven org types collapsed into
    one (`tier1_ror.ROR_RESEARCH_TYPES`), which is the right granularity for
    *routing* — every one of those seven takes the same branch — and the wrong
    granularity for the *answer*. ``ror_org_types`` carries what ROR actually
    said, so ``government`` survives to the output.

    The boolean is still authoritative for everything else: types are read only
    to separate a public body from the rest, never to overturn ROR's own
    research/company split.
    """
    types = {t.strip().lower() for t in (ev.ror_org_types or ())}
    if types & _ROR_GOVERNMENT_TYPES:
        return GOVERNMENT
    if ev.ror_is_research is None:
        return None
    return RESEARCH if ev.ror_is_research else COMPANY


def _from_legal_form(ev: TypeEvidence) -> str | None:
    """Character of the entity's ISO 20275 legal form, or ``None``."""
    code = (ev.gleif_legal_form_id or "").strip().upper()
    if code in NON_COMMERCIAL_ELF:
        return RESEARCH
    if code in COMMERCIAL_ELF:
        return COMPANY
    # 8888 / 9999 / unknown code — the free text is the only thing left.
    other = (ev.gleif_legal_form_other or "").strip().lower()
    if not other:
        return None
    if any(tok in other for tok in _OTHER_NON_COMMERCIAL):
        return RESEARCH
    if any(tok in other for tok in _OTHER_COMMERCIAL):
        return COMPANY
    return None


def _from_gleif(ev: TypeEvidence) -> str | None:
    """GLEIF entity metadata, with the LEI guard applied.

    **An LEI hit on its own never sets ``company``.** An LEI proves legal
    registration, not commercial status: universities, hospitals, foundations
    and government bodies hold LEIs, typically for bond issuance or derivatives
    reporting. So a commercial-looking verdict is withheld — not overridden,
    withheld — whenever the name itself reads as a research institution, and the
    keyword source below then answers. The two facts are not in conflict: a
    university with an LEI is a university that issues bonds.
    """
    if not ev.lei_id:
        return None

    verdict = _CATEGORY_TYPE.get((ev.gleif_category or "").strip().upper())
    if verdict is None:
        verdict = _from_legal_form(ev)
    if verdict is None:
        return None
    if verdict is COMPANY and looks_like_research_institution(ev.name1):
        return None
    return verdict


def _from_legal_suffix(ev: TypeEvidence) -> str | None:
    """A corporate legal form in Name 1, or ``None``.

    Symmetric counterpart to :func:`_from_keyword`, and can only ever yield
    ``COMPANY`` for the same reason that one can only yield ``RESEARCH``: the
    absence of a legal suffix is not evidence of anything. Plenty of companies
    trade under a name that omits it.
    """
    return COMPANY if has_corporate_legal_suffix(ev.name1) else None


def _from_keyword(ev: TypeEvidence) -> str | None:
    return RESEARCH if looks_like_research_institution(ev.name1) else None


def classify(evidence: TypeEvidence) -> tuple[str, str]:
    """Decide ``record_type`` once. Returns ``(record_type, record_type_source)``.

    ``record_type_source`` is one of ``ror`` | ``gleif`` | ``legal_form`` |
    ``keyword`` | ``unresolved`` and always matches the verdict: an ``unknown``
    type always reports ``unresolved``, and never the other way round.
    """
    for source, fn in (
        (SOURCE_ROR, _from_ror),
        (SOURCE_GLEIF, _from_gleif),
        (SOURCE_LEGAL_FORM, _from_legal_suffix),
        (SOURCE_KEYWORD, _from_keyword),
    ):
        verdict = fn(evidence)
        if verdict is not None:
            return verdict, source
    return UNKNOWN, SOURCE_UNRESOLVED
