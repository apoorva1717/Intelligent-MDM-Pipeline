"""Delivery-point parsing and comparison (v2, ``DEDUP_V2_BLOCKING``).

v1 blocks on ``sha1(country | postal_code | street | house_no)`` with each part
only case- and punctuation-folded (dedup/signatures.py:51-62). Two records at
one door therefore fall into different blocks whenever the address was typed
differently — ``1855 Folsom St`` in the street line against house number
``1855`` and street ``Folsom St``, ``FM 521`` against ``FM 521 Rd``, a zip with
a transposed digit — and nothing downstream can merge across blocks. That is
the single largest source of missed duplicates in the stress batch.

This module makes the *delivery point* the block key instead of the spelling:

    country | zip5 | house

with the house number recovered from the street line when the house column is
empty, and the street line reduced to a comparable core. Two secondary rules
follow from that choice:

``K2`` (``country | city | house``)
    A zip typo would otherwise split a door in two. City plus house number
    recovers it; the pairwise zip check in :func:`address_compatible` is what
    stops that key being abused, by refusing a pair whose zips differ by more
    than one edit.

the fallback block (``country | zip5``)
    A row with no usable house number names no delivery point. It does NOT
    join a house-bearing block — an address we cannot verify is not evidence
    that two records share a door, and letting such a row attach to its
    neighbours is how a bare "NASA, Intelligent Systems Division" with no
    street silently collapses into a specific building's cluster. House-less
    rows block only with each other, and every cluster they form is routed to
    manual review. Their relationship to the verified blocks is a Link ID
    (shared ROR/LEI or name), never a Cluster ID.

Everything here is pure and deterministic: no LLM, no network, no I/O.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, List, Literal, Optional, Sequence

from rapidfuzz.distance import DamerauLevenshtein, JaroWinkler

from dedup.models import DedupRow
from dedup.signatures import normalize_key

#: Street-type words that END the comparable part of a street line. Everything
#: after the first one is delivery detail, not the street: "Rd 113",
#: "Dr Truck 2". Canonicalised to one spelling so "TPK", "Tpke" and "Turnpike"
#: compare equal.
STREET_TYPES: dict[str, str] = {
    "rd": "road", "road": "road",
    "st": "street", "street": "street",
    "dr": "drive", "drive": "drive",
    "ave": "avenue", "avenue": "avenue",
    "fwy": "freeway", "freeway": "freeway",
    "tpk": "turnpike", "tpke": "turnpike", "turnpike": "turnpike",
    "pkwy": "parkway", "parkway": "parkway",
    "ln": "lane", "lane": "lane",
    "blvd": "boulevard", "boulevard": "boulevard",
    "ct": "court", "court": "court",
    "hwy": "highway", "highway": "highway",
}

#: Compass words. Canonicalised like the street types, but they never END the
#: street core: "Charles E Young Dr So" must keep "Young", and terminating on
#: the leading "E" of "E 11 Mile Rd" would leave nothing at all.
DIRECTIONALS: dict[str, str] = {
    "n": "north", "north": "north",
    "s": "south", "south": "south", "so": "south",
    "e": "east", "east": "east",
    "w": "west", "west": "west",
}

STREET_SUFFIXES: dict[str, str] = {**STREET_TYPES, **DIRECTIONALS}

#: A house number as written in a street line, AFTER normalisation has folded
#: its punctuation away: 45, 45A, and 47-111 (which arrives here as 47111).
_HOUSE_TOKEN = re.compile(r"^\d+[a-z]?$")
_DIGITS = re.compile(r"\d")
_PURE_NUMBER = re.compile(r"^\d+$")

#: Street cores this close count as the same street.
STREET_NAME_THRESHOLD = 0.85

#: How far two zips may drift and still be one delivery point: a single edit
#: (including a transposition — 90003/90030 is one keystroke, not two).
ZIP_EDIT_TOLERANCE = 1

CompatLabel = Literal["exact", "fuzzy", "partial", "incompatible"]
StreetLabel = Literal["exact", "fuzzy", "differs", "unknown"]


@dataclass(frozen=True)
class ParsedAddress:
    """One row's delivery point, reduced to what can be compared."""

    country: str
    zip5: str
    house: str
    street_core: str
    city_norm: str
    #: A number that looked like a house but left no street behind it — a
    #: street line of just "38". Kept because it is worth showing a reviewer,
    #: and deliberately NOT used as a house: a number with no street names no
    #: door.
    house_hint: str = ""

    @property
    def house_less(self) -> bool:
        """True when this row names no usable delivery point."""
        return not self.house


def _normalise_house(value: str) -> str:
    """``45A`` → ``45a``, ``47-111`` → ``47111``."""
    return re.sub(r"[^0-9a-z]", "", value.strip().lower())


def _zip5(country: str, postal_code: Optional[str]) -> str:
    """First five digits for US postcodes; the normalised value elsewhere.

    Only the US has a five-digit delivery zip with a four-digit extension, so
    truncating anything else would fold together postcodes that name different
    places (a UK outward+inward code, a Dutch 1234 AB).
    """
    raw = (postal_code or "").strip()
    if not raw:
        return ""
    if country.upper() in ("US", "USA"):
        digits = "".join(_DIGITS.findall(raw))
        return digits[:5] if len(digits) >= 5 else normalize_key(raw)
    return normalize_key(raw)


def _street_core(tokens: Sequence[str]) -> str:
    """The comparable part of a street line: name plus its street type.

    Canonicalises spellings, stops at the first street type, and drops
    whatever followed it (a dock, a room, a route number).
    """
    core: List[str] = []
    for raw in tokens:
        token = normalize_key(raw)
        if not token:
            continue
        # normalize_key can split one raw token ("S." → "s"); take it apart the
        # same way so the suffix table still sees single words.
        for word in token.split():
            canonical = STREET_SUFFIXES.get(word, word)
            core.append(canonical)
            if word in STREET_TYPES:
                return " ".join(core)
    return " ".join(core)


def parse_address(row: DedupRow) -> ParsedAddress:
    """Reduce one row's address columns to a comparable delivery point."""
    country = (row.country or "").strip().upper()
    zip5 = _zip5(country, row.postal_code)
    city_norm = normalize_key(row.city)

    street_tokens = (row.street or "").split()
    house = _normalise_house(row.house_no or "")
    house_hint = ""

    if house:
        rest = street_tokens
    else:
        rest = street_tokens
        candidate = ""
        if street_tokens:
            if _HOUSE_TOKEN.match(normalize_key(street_tokens[0]).replace(" ", "")):
                candidate, rest = street_tokens[0], street_tokens[1:]
            elif _HOUSE_TOKEN.match(normalize_key(street_tokens[-1]).replace(" ", "")):
                candidate, rest = street_tokens[-1], street_tokens[:-1]
        if candidate:
            # A number the street line opened or closed with is only a house
            # number if a street remains beside it. A street line of "38" is a
            # fragment of something lost upstream, and treating it as a door
            # would block this row against every real house 38 in the zip.
            if _street_core(rest):
                house = _normalise_house(candidate)
            else:
                house_hint = _normalise_house(candidate)
                rest = street_tokens

    return ParsedAddress(
        country=country,
        zip5=zip5,
        house=house,
        street_core=_street_core(rest),
        city_norm=city_norm,
        house_hint=house_hint,
    )


# ---------------------------------------------------------------------------
# Block keys
# ---------------------------------------------------------------------------

def block_keys(parsed: ParsedAddress) -> List[str]:
    """Every block key this address belongs to, in a stable order.

    A row with a house number emits the zip key and — when both zip and city
    are known — the city key too, so a zip typo cannot split its door. A
    house-less row emits only the fallback key for its (country, zip): it
    blocks with other house-less rows and with nobody else.

    The prefixes keep the three key spaces from ever colliding, which is what
    makes "this block holds only house-less rows" a property of the key rather
    than something the caller has to re-derive.
    """
    if parsed.house_less:
        return [f"f:{parsed.country}|{parsed.zip5}"]
    keys = [f"z:{parsed.country}|{parsed.zip5}|{parsed.house}"]
    if parsed.zip5 and parsed.city_norm:
        keys.append(f"c:{parsed.country}|{parsed.city_norm}|{parsed.house}")
    return keys


# ---------------------------------------------------------------------------
# Pairwise comparison
# ---------------------------------------------------------------------------

def _numeric_tokens(core: str) -> set[str]:
    return {t for t in core.split() if _PURE_NUMBER.match(t)}


def streets_compatible(left: str, right: str) -> bool:
    """Whether two street cores name the same street.

    Jaro-Winkler alone is not enough, and the failure is not marginal: it reads
    ``E Qume Dr`` against ``Qume Dr`` at 0.72 because a missing leading word
    wrecks a prefix-weighted metric. So a token rule stands beside it — most of
    the shorter side must find a counterpart on the other — with a numeric
    guard so that ``11 Mile Rd`` can never satisfy it against ``13 Mile Rd``.
    """
    if not left or not right:
        return False

    # A house number lives in its own field; a number inside the street core is
    # part of the street's NAME ("11 Mile", "FM 521", "4th"). Two streets that
    # disagree on one are different streets, however well the words match — and
    # they match very well: "11 mile road" against "13 mile road" is one
    # character, which Jaro-Winkler reads at 0.94. So this is a veto over BOTH
    # tests below, not a clause of the second one.
    left_numbers, right_numbers = _numeric_tokens(left), _numeric_tokens(right)
    if left_numbers and right_numbers and left_numbers != right_numbers:
        return False

    if JaroWinkler.similarity(left, right) >= STREET_NAME_THRESHOLD:
        return True

    left_tokens, right_tokens = left.split(), right.split()
    shorter, longer = (
        (left_tokens, right_tokens)
        if len(left_tokens) <= len(right_tokens)
        else (right_tokens, left_tokens)
    )
    needed = 1 if len(shorter) == 1 else max(2, math.ceil(len(shorter) / 2))
    matched = sum(
        1
        for token in shorter
        if any(
            JaroWinkler.similarity(token, other) >= STREET_NAME_THRESHOLD
            for other in longer
        )
    )
    return matched >= needed


def address_compatible(a: ParsedAddress, b: ParsedAddress) -> CompatLabel:
    """Can these two rows be at one delivery point?

    ``incompatible`` is the only load-bearing answer — it means "do not even
    ask the model about this pair". The other three grade how much of the
    address was actually comparable, and are informational.
    """
    if a.house and b.house and a.house != b.house:
        return "incompatible"
    if (
        a.zip5
        and b.zip5
        and a.zip5 != b.zip5
        and DamerauLevenshtein.distance(a.zip5, b.zip5) > ZIP_EDIT_TOLERANCE
    ):
        return "incompatible"
    if a.street_core and b.street_core and not streets_compatible(a.street_core, b.street_core):
        return "incompatible"

    parts = (
        (a.zip5, b.zip5),
        (a.house, b.house),
        (a.street_core, b.street_core),
    )
    if any(not left or not right for left, right in parts):
        return "partial"
    if all(left == right for left, right in parts):
        return "exact"
    return "fuzzy"


def street_match(a: ParsedAddress, b: ParsedAddress) -> StreetLabel:
    """The street verdict as the model is told it.

    Deliberately NOT the same vocabulary as :func:`address_compatible`.
    ``differs`` says the two street STRINGS did not pass the comparison — the
    records are in one block, so their delivery point already matched on zip
    and house number. Telling a model "incompatible" about a pair it is being
    asked to judge invites it to reject on an address question that has
    already been decided.
    """
    if not a.street_core or not b.street_core:
        return "unknown"
    if a.street_core == b.street_core:
        return "exact"
    return "fuzzy" if streets_compatible(a.street_core, b.street_core) else "differs"


def any_compatible(
    left: Iterable[ParsedAddress], right: Iterable[ParsedAddress]
) -> bool:
    """True unless EVERY cross pair is incompatible.

    Used to gate whole entities, which can hold several rows. One comparable
    pair is enough to justify asking the model; requiring all of them would let
    a single mistyped member veto a merge the rest of the evidence supports.
    """
    left, right = list(left), list(right)
    if not left or not right:
        return True
    return any(
        address_compatible(a, b) != "incompatible" for a in left for b in right
    )
