"""One locality comparator, shared by the page read and the registries.

Fix D(2) asks for "the location comparator built for the page-read
corroborator" to be applied to ROR and GLEIF matches as well. That comparator
was written inside ``enrichment.page_corroborator``, and it cannot simply be
imported from ``tier1_ror`` / ``tier1_lei``: the corroborator already imports
both of them, so the dependency would close a cycle. Rather than write a
second comparator that is "the same rules, roughly", the rules live here —
a leaf module that imports nothing from ``enrichment`` — and both sides use
this one.

Nothing about the rules changed in the move. They are, verbatim, the three
that the corroborator was designed around and that the chemspeed batch tuned:

**Silence is not evidence.** A source that states no place neither
corroborates nor contradicts the record. ``"neutral"`` is the default and the
common case.

**Only a stated place that differs is a contradiction.** A matching postal
code settles it outright; otherwise country, then region, then city, in that
order of strength.

**The granularity of the disagreement is part of the answer.** Two cities in
one state are routinely one organisation's plant and head office (Houston /
Baytown, Texas); two states or two countries are not. Callers that act on a
contradiction read ``scope`` and decide what they will act on — the page-read
withdrawal rule requires ``region`` or ``country``.

A US region is normalised so ``CA`` and ``California`` compare equal, which is
what :data:`US_REGION_CODES` is for. The map lives here and is re-exported by
``tier1_ror`` under its historical private name, so the "ROR-local expansion
maps never touch an output name" structural test keeps testing the same object.
"""

from __future__ import annotations

import re

from utils.text_utils import country_to_iso_code

#: US two-letter postal code → state name. A bare two-letter token in a Region
#: field can only be the state, so expanding it for a COMPARISON is safe —
#: which is exactly the licence this map is used under here and in
#: ``tier1_ror._expand_state_abbrevs``. It must never expand an output name
#: ("IN Laboratories"), and a structural test in
#: ``tests/test_registry_name_authority.py`` pins that.
US_REGION_CODES: dict[str, str] = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas",
    "ca": "California", "co": "Colorado", "ct": "Connecticut",
    "de": "Delaware", "fl": "Florida", "ga": "Georgia", "hi": "Hawaii",
    "id": "Idaho", "il": "Illinois", "in": "Indiana", "ia": "Iowa",
    "ks": "Kansas", "ky": "Kentucky", "la": "Louisiana", "me": "Maine",
    "md": "Maryland", "ma": "Massachusetts", "mi": "Michigan",
    "mn": "Minnesota", "ms": "Mississippi", "mo": "Missouri",
    "mt": "Montana", "ne": "Nebraska", "nv": "Nevada",
    "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico",
    "ny": "New York", "nc": "North Carolina", "nd": "North Dakota",
    "oh": "Ohio", "ok": "Oklahoma", "or": "Oregon",
    "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
    "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah",
    "vt": "Vermont", "va": "Virginia", "wa": "Washington",
    "wv": "West Virginia", "wi": "Wisconsin", "wy": "Wyoming",
}

#: The three verdicts. Never a bare boolean — "the source said nothing" is a
#: third answer and collapsing it into either of the other two is the mistake
#: this module exists to prevent.
CONSISTENT = "consistent"
CONTRADICTED = "contradicted"
NEUTRAL = "neutral"

_WS_RE = re.compile(r"\s+")


def _norm(value: str | None) -> str:
    return _WS_RE.sub(" ", (value or "").strip().lower())


def normalise_country(value: str | None) -> str:
    """A country normalised so "US" and "United States" compare equal.

    Measured on the chemspeed batch: without this, six ROR matches were
    reported as contradicting their own record, because ROR states
    ``country_name`` ("United States") and the SAP record carries the ISO code
    ("US"). Two spellings of one country are not a disagreement, and reporting
    them as one is exactly the false signal this comparator exists to avoid.

    An unrecognised name folds to its own lowercase form, so two spellings this
    map does not know still compare equal to each other and unequal to a
    different country.
    """
    text = _norm(value)
    if not text:
        return ""
    return (country_to_iso_code(text) or text).lower()


#: An ISO 3166-2 subdivision code: the country, a hyphen, and the subdivision
#: ("US-TX", "GB-ENG", "DE-BY"). GLEIF writes regions this way, ROR's
#: ``country_subdivision_code`` sometimes does, and an SAP record carries the
#: bare code — three spellings of one place.
#:
#: The pattern is deliberately narrow. Stripping at the LAST hyphen instead
#: would turn "Nord-Pas-de-Calais" into "Calais" and "Provence-Alpes-Côte
#: d'Azur" into "Côte d'Azur", inventing a region nobody named; anchoring on
#: two leading letters and a short alphanumeric tail matches the ISO shape and
#: nothing else. A region whose own name happens to be hyphenated is left
#: exactly as written.
_ISO_SUBDIVISION_RE = re.compile(r"^([a-z]{2})-([a-z0-9]{1,3})$", re.IGNORECASE)


def strip_subdivision_prefix(value: str | None) -> str:
    """``"US-TX"`` → ``"TX"``; anything that is not an ISO 3166-2 code is
    returned unchanged.

    Lives here, beside the comparison it exists to serve, rather than at the
    point each registry response is parsed. A prefix stripped at ONE parse site
    normalises one lane and leaves every other caller comparing "us-tx" against
    "texas" — which is the defect this function was extracted for: the record
    matched to LEI 3YTEJFW18LGIUQ2N5J61 (legal address US-DE, headquarters
    US-TX) against a record stating TX. Whoever hands a region to
    :func:`normalise_region` now gets the same answer, whichever spelling of
    the region they happen to hold.
    """
    text = (value or "").strip()
    match = _ISO_SUBDIVISION_RE.match(text)
    return match.group(2) if match else text


def region_label(value: str | None) -> str:
    """*value* rendered for a human: "DE" becomes "Delaware".

    The reason text on ``registry-location-mismatch`` reads "…states region DE;
    record says NJ", and on a US batch "DE" is Delaware far more often than it
    is Germany — a reviewer should not have to work that out. Only used for
    prose; the comparison itself runs on :func:`normalise_region`.

    The ISO prefix is dropped here too, so the prose quotes the region the
    comparison actually ran on. A reason reading "states region US-TX" invites
    a reviewer to wonder whether the mismatch IS the prefix.
    """
    text = strip_subdivision_prefix(value)
    expanded = US_REGION_CODES.get(text.strip(". ").lower())
    return f"{text} ({expanded})" if expanded and expanded.lower() != text.lower() else text


def normalise_region(value: str | None) -> str:
    """A region normalised so "US-TX", "TX" and "Texas" compare equal.

    Without the US map, `San Francisco, California` on a page "contradicts"
    `San Francisco, CA` on the record — measured on the chemspeed batch, where
    it produced a false contradiction on Anresco Laboratories. Without the ISO
    strip in front of it, a registry writing "US-TX" contradicts a record
    saying "TX", which is one place spelled two ways and no disagreement at
    all — see :func:`strip_subdivision_prefix`.
    """
    text = _norm(strip_subdivision_prefix(value)).strip(". ")
    if not text:
        return ""
    # The map's values are title-cased ("ca" -> "California"); both sides of
    # the comparison are lowercase here.
    return US_REGION_CODES.get(text, text).lower()


def postal_matches(stated: str | None, record: str | None) -> bool:
    """Postal codes compared on digits/letters only — "12345-6789" and "12345"
    are the same place written two ways, and a 5-digit ZIP is the part both
    sides always carry."""
    a = re.sub(r"[^a-z0-9]", "", (stated or "").lower())[:5]
    b = re.sub(r"[^a-z0-9]", "", (record or "").lower())[:5]
    return bool(a) and a == b


def compare_locality(
    *,
    stated_city: str | None = None,
    stated_region: str | None = None,
    stated_country: str | None = None,
    stated_postal_code: str | None = None,
    city: str | None = None,
    region: str | None = None,
    country: str | None = None,
    postal_code: str | None = None,
) -> tuple[str, str | None, str | None]:
    """``(verdict, detail, scope)`` for one stated place against one record.

    *verdict* is :data:`CONSISTENT`, :data:`CONTRADICTED` or :data:`NEUTRAL`.
    *scope* names the granularity the verdict was reached at — ``"postal"``,
    ``"city"``, ``"region"`` or ``"country"`` — and is ``None`` when the
    verdict is neutral.
    """
    if not any((stated_city, stated_region, stated_country, stated_postal_code)):
        return NEUTRAL, None, None

    if postal_matches(stated_postal_code, postal_code):
        return CONSISTENT, f"postal {stated_postal_code}", "postal"

    s_city, r_city = _norm(stated_city), _norm(city)
    s_region, r_region = normalise_region(stated_region), normalise_region(region)
    s_country = normalise_country(stated_country)
    r_country = normalise_country(country)

    # Country first, and only when both sides state one: a different country is
    # the strongest disagreement available and is not softened by a city that
    # happens to share a name.
    if s_country and r_country and s_country != r_country:
        return CONTRADICTED, (
            f"states country {stated_country}; record says {country}"
        ), "country"

    if s_region and r_region and s_region != r_region:
        return CONTRADICTED, (
            f"states region {region_label(stated_region)}; "
            f"record says {region_label(region)}"
        ), "region"

    if s_city and r_city:
        if s_city == r_city:
            return CONSISTENT, f"city {stated_city}", "city"
        return CONTRADICTED, (
            f"states city {stated_city}; record says {city}"
        ), "city"

    if s_region and r_region:
        return CONSISTENT, f"region {stated_region}", "region"

    # A stated country that matches, with nothing finer, is too coarse to
    # corroborate a US SMB — every candidate in a US batch would pass.
    return NEUTRAL, None, None


# ── The registry comparator ───────────────────────────────────────────────
#
# A registry states more than one address for one entity, and the record
# states one. GLEIF publishes both ``Entity.LegalAddress`` (where the entity is
# incorporated) and ``Entity.HeadquartersAddress`` (where it operates from);
# ROR publishes the one primary location. Comparing a US record against the
# legal address ALONE is the single largest source of false contradictions on
# this batch: eleven of nineteen flags on the chemspeed run read "GLEIF states
# region DE (Delaware)", because Delaware is where the company is registered
# and not where it is. AdvanSix is incorporated in Wilmington DE and
# headquartered in Parsippany NJ; the record says NJ, and the registry agrees
# with it — on the address the registry publishes for exactly that purpose.
#
# So the comparison is against the SET of addresses, and the aggregation is
# asymmetric on purpose:
#
#   corroborated  — the record agrees with ANY registered address. One
#                   agreement is a positive identification; the other address
#                   naming a different place is not evidence against it,
#                   because the two addresses are not competing claims about
#                   one place. They are two true statements about one entity.
#   contradicted  — the record agrees with NONE of them and disagrees with at
#                   least one. Only then has the registry, on everything it
#                   publishes, failed to put the entity where the record does.
#   neutral       — every address was silent.
#
# The second departure from :func:`compare_locality` is granularity. Two
# cities inside one agreeing region are one organisation's plant and head
# office (Altria: SUFFOLK on the legal address, RICHMOND on the headquarters,
# both Virginia, and the record says Richmond VA). The region agreeing IS the
# corroboration; the city differing inside it is noise, and it is recorded in
# the trace rather than acted on. `compare_locality` itself is left alone —
# the page-read corroborator is a different question (one witness, one stated
# place) and its withdrawal rule already reads `scope`.

#: Granularity, strongest first. Used to choose which of several agreeing
#: addresses gets to describe the verdict — a postal-code match says more than
#: a country match, and the reason text should quote the stronger one.
_SCOPE_RANK: dict[str | None, int] = {
    "postal": 0, "city": 1, "region": 2, "country": 3, None: 4,
}


def _stated_place(address: dict[str, str | None], scope: str | None) -> str:
    """The registry's side of a disagreement, at the granularity it happened."""
    if scope == "country":
        return f"country {address.get('country')}"
    if scope == "region":
        return f"region {region_label(address.get('region'))}"
    return f"city {address.get('city')}"


def _record_place(
    scope: str | None,
    city: str | None,
    region: str | None,
    country: str | None,
) -> str:
    if scope == "country":
        return str(country)
    if scope == "region":
        return region_label(region)
    return str(city)


def compare_registry_addresses(
    addresses: list[dict[str, str | None]],
    *,
    city: str | None = None,
    region: str | None = None,
    country: str | None = None,
    postal_code: str | None = None,
) -> tuple[str, str | None, str | None, list[str]]:
    """``(verdict, detail, scope, notes)`` for one registry's addresses.

    *addresses* is every address the registry publishes for the matched entity,
    each a mapping of ``city`` / ``region`` / ``country`` / ``postal_code``
    plus a ``kind`` naming it ("legal", "headquarters", "registered") for the
    trace. Order does not affect the verdict.

    *notes* are the observations that did NOT reach the verdict — the
    city-level differences inside an agreeing region. They belong in the trace
    and nowhere else: acting on them is what produced the false Altria flag.
    """
    per_address: list[tuple[str, str | None, str | None, dict[str, str | None]]] = []
    notes: list[str] = []

    for address in addresses:
        if not isinstance(address, dict):
            continue
        kind = str(address.get("kind") or "registered")
        verdict, detail, scope = compare_locality(
            stated_city=address.get("city"),
            stated_region=address.get("region"),
            stated_country=address.get("country"),
            stated_postal_code=address.get("postal_code"),
            city=city, region=region, country=country, postal_code=postal_code,
        )
        # Two cities inside one agreeing region: the region agreeing is the
        # answer, and the city is a note.
        if verdict == CONTRADICTED and scope == "city":
            stated_region = normalise_region(address.get("region"))
            if stated_region and stated_region == normalise_region(region):
                notes.append(
                    f"{kind} address {detail} — same region, not a contradiction"
                )
                verdict, detail, scope = (
                    CONSISTENT, f"region {region_label(address.get('region'))}",
                    "region",
                )
        per_address.append((verdict, detail, scope, address))

    agreeing = [e for e in per_address if e[0] == CONSISTENT]
    if agreeing:
        # The strongest agreement describes the verdict.
        verdict, detail, scope, _ = min(
            agreeing, key=lambda e: _SCOPE_RANK.get(e[2], 4),
        )
        return CONSISTENT, detail, scope, notes

    disagreeing = [e for e in per_address if e[0] == CONTRADICTED]
    if not disagreeing:
        return NEUTRAL, None, None, notes

    # Every address the registry publishes puts the entity somewhere the
    # record does not. Quote the coarsest disagreement — a different country
    # is the claim worth leading with when both are available.
    disagreeing.sort(key=lambda e: -_SCOPE_RANK.get(e[2], 4))
    _, _, scope, _ = disagreeing[0]
    stated = list(dict.fromkeys(
        _stated_place(address, sc) for _, _, sc, address in disagreeing
    ))
    record_side = _record_place(scope, city, region, country)
    if len(stated) == 1:
        return CONTRADICTED, f"states {stated[0]}; record says {record_side}", scope, notes
    labelled = [
        f"{_stated_place(address, sc)} ({address.get('kind') or 'registered'})"
        for _, _, sc, address in disagreeing
    ]
    return (
        CONTRADICTED,
        f"states {' and '.join(labelled)}; record says {record_side}",
        scope,
        notes,
    )
