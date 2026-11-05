"""Peppol Directory: a German entity's VAT ID from its Peppol participant ID.

An entity that receives e-invoices over Peppol registers itself in the public
Peppol Directory. Under participant scheme ``9930`` its identifier IS its
German VAT ID (``9930:de811128135``), so an entry is the entity's own
statement of its number: source class A (``PEPPOL_DIRECTORY``). Common for
German public bodies, which must accept e-invoices (XRechnung).

Searched by name (the API matches every word; umlauts as written). Only
scheme 9930 is read; a Leitweg-ID (scheme 0204) is not a VAT ID. Names carry
a company-code suffix now and then ("Robert Bosch GmbH (5000)"), which is
dropped. Most entries state no address, so most hits are ``NO_ADDRESS`` and
go to review; an entry whose ``geoInfo`` names the record's town can be
accepted.

Answers are cached in the evidence cache (``vat_peppol``), so CACHE_FROZEN
covers them; with no recording a frozen run does not ask.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from vat.entity_match import (
    ADDRESS_DIFFERENT_TOWN,
    ADDRESS_LEVELS,
    CONFLICTING_SOURCES,
    LEGAL_FORM_MISSING_IN_SAP,
    NAME_EXACT,
    SAP_NAME_LIMIT,
    SISTER_ENTITY_ON_PAGE,
    SOURCE_PEPPOL_DIRECTORY,
    address_match,
    match_score,
    name_match,
    parse_name,
)
from vat.lookup import (
    FOUND_UNVERIFIED,
    FOUND_PENDING,
    FOUND_VERIFIED,
    VALIDATION_PENDING,
    VALIDATION_UNAVAILABLE,
    FoundResult,
    LookupInput,
    today,
)
from vat.normalize import FORMAT_OK, assess_format
from vat.vies_client import INVALID, VALID, VIESClient

logger = logging.getLogger(__name__)

PEPPOL_SEARCH_URL = "https://directory.peppol.eu/search/1.0/json"
#: The page a steward opens for one participant.
PEPPOL_PARTICIPANT_URL = "https://directory.peppol.eu/public/locale-en_US/menuitem-search?q={pid}"
#: Participant scheme whose identifier is a German VAT ID.
VAT_SCHEME = "9930"
#: Leitweg-ID (German public bodies: universities, hospitals, authorities).
#: Some bodies use their USt-IdNr. as the fine address:
#: ``0204:08222000-de143845342-36`` is Universität Mannheim's DE143845342.
LEITWEG_SCHEME = "0204"
#: VAT_Found_Flags: the number was read out of a Leitweg-ID.
LEITWEG_FLAG = "PEPPOL_LEITWEG_ID"
MAX_RESULTS = 20
TIMEOUT_SECONDS = 15.0
NAMESPACE = "vat_peppol"
#: VAT_Found_Flags: the result came from the Peppol Directory.
PEPPOL_FLAG = "PEPPOL_DIRECTORY"

_VAT_VALUE_RE = re.compile(r"^(?:de)?(\d{9})$", re.IGNORECASE)
_LEITWEG_VAT_RE = re.compile(r"(?:^|-)de(\d{9})(?:-|$)", re.IGNORECASE)
_CODE_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")


@dataclass(frozen=True)
class PeppolEntry:
    vat_id: str           # DE + 9 digits
    name: str
    geo: str | None       # geoInfo: free text, often absent
    participant: str      # 9930:de811128135


def parse_matches(body: Any) -> list[PeppolEntry]:
    """German entries of a search response that carry a VAT ID, without
    repeats: scheme 9930 (the identifier is the VAT ID), and scheme 0204
    (Leitweg-ID) when its fine address is a DE VAT ID."""
    out: list[PeppolEntry] = []
    seen: set[tuple[str, str]] = set()
    for match in (body or {}).get("matches") or []:
        pid = ((match.get("participantID") or {}).get("value") or "").strip()
        scheme, _, value = pid.partition(":")
        if scheme == VAT_SCHEME:
            digits = _VAT_VALUE_RE.match(value.strip())
        elif scheme == LEITWEG_SCHEME:
            digits = _LEITWEG_VAT_RE.search(value.strip())
        else:
            digits = None
        if not digits:
            continue
        for entity in match.get("entities") or []:
            if (entity.get("countryCode") or "DE").upper() != "DE":
                continue
            names = [n.get("name") for n in entity.get("name") or [] if n.get("name")]
            if not names:
                continue
            name = _CODE_SUFFIX_RE.sub("", names[0]).strip()
            vat = "DE" + digits.group(1)
            if (vat, name) in seen:
                continue
            seen.add((vat, name))
            out.append(PeppolEntry(vat, name, (entity.get("geoInfo") or None),
                                   f"{scheme}:{value.lower()}"))
    return out


def search_name(name_1: str) -> str:
    """Name 1 as the search term, without a fragment SAP's 35-character cut
    left behind ("… Deutschland Gm")."""
    words = name_1.split()
    if len(name_1.strip()) >= SAP_NAME_LIMIT - 2 and len(words) > 1 and len(words[-1]) <= 3 \
            and parse_name(name_1, sap=True).core != parse_name(name_1).core:
        words = words[:-1]
    return " ".join(words)


class PeppolDirectory:
    """The Peppol Directory search API, through the evidence cache."""

    def __init__(
        self, cache, *, enabled: bool = True, transport: httpx.AsyncBaseTransport | None = None,
        write_failures: Any = None,
    ) -> None:
        self._store = cache.namespace(NAMESPACE)
        self.enabled = enabled
        self._transport = transport
        self._write_failures = write_failures
        self.network_calls = 0
        self.cache_hits = 0

    async def search(self, name: str) -> list[PeppolEntry] | None:
        """Entries for *name*; None when the directory could not be asked
        (switched off, a frozen run with no recording, an error)."""
        key = f"name={name}|country=DE"
        recorded = self._store.get(key)
        if isinstance(recorded, dict):
            self.cache_hits += 1
            return parse_matches(recorded)
        if self._store.replay_only or not self.enabled:
            return None
        self.network_calls += 1
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=TIMEOUT_SECONDS) as client:
                resp = await client.get(PEPPOL_SEARCH_URL, params={
                    "name": name, "country": "DE", "rpc": MAX_RESULTS,
                })
            if resp.status_code != 200:
                logger.warning("Peppol Directory answered %s for %r", resp.status_code, name)
                return None
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Peppol Directory search failed for %r: %s", name, exc)
            return None
        if self._write_failures is not None:
            self._write_failures.set_checked(self._store, key, body)
        else:
            self._store.set(key, body)
        return parse_matches(body)


async def peppol_result(
    directory: PeppolDirectory, vies: VIESClient | None, inp: LookupInput, mode: str,
) -> FoundResult | None:
    """The record's entity in the Peppol Directory, judged like a page: the
    entry's name must be the record's exact entity; its geoInfo is the
    address. None when no entry names the entity."""
    if not inp.name_1:
        return None
    entries = await directory.search(search_name(inp.name_1))
    if not entries:
        return None
    judged = []
    for entry in entries:
        verdict = name_match(entry.name, inp.names)
        if verdict.match != NAME_EXACT:
            continue
        level, parts = address_match(entry.geo or "", inp.street, inp.house_number,
                                     inp.postal_code, inp.city)
        judged.append((entry, verdict, level, parts))
    if not judged:
        return None
    entry, verdict, level, parts = min(
        judged, key=lambda j: (ADDRESS_LEVELS.index(j[2]), len(j[1].issues)))
    flags = [PEPPOL_FLAG, f"PEPPOL_PARTICIPANT:{entry.participant}"]
    if entry.participant.startswith(LEITWEG_SCHEME + ":"):
        flags.append(LEITWEG_FLAG)
    # Another number for the same entity, not in another town: a conflict.
    values = sorted({e.vat_id for e, _, lv, _ in judged if lv != ADDRESS_DIFFERENT_TOWN})
    if len(values) > 1:
        flags.append(f"{CONFLICTING_SOURCES}:{','.join(values)}")
    issues = list(verdict.issues)
    if LEGAL_FORM_MISSING_IN_SAP in issues and any(
            parse_name(e.name).core == verdict.core and parse_name(e.name).form != verdict.page_form
            for e in entries):
        issues.append(SISTER_ENTITY_ON_PAGE)

    fmt = assess_format(entry.vat_id, "DE")
    validation, date = None, None
    status = FOUND_UNVERIFIED
    if fmt.status == FORMAT_OK and vies is None:  # Step AF-2: the queue asks VIES
        status, validation = FOUND_PENDING, VALIDATION_PENDING
    elif fmt.status == FORMAT_OK:
        answer = await vies.check(fmt)
        if answer.error_code:
            flags.append(f"VIES:{answer.error_code}")
        status = FOUND_VERIFIED if answer.status == VALID else f"{FOUND_UNVERIFIED}:VIES_{answer.status}"
        validation = answer.status if answer.status in (VALID, INVALID) else VALIDATION_UNAVAILABLE
        date = answer.request_date
    evidence = f"Peppol participant {entry.participant}: {entry.name}"
    if entry.geo:
        evidence += f" ({entry.geo})"
    return FoundResult(
        mode, status, value=fmt.normalized or entry.vat_id,
        source_url=PEPPOL_PARTICIPANT_URL.format(pid=entry.participant),
        attributed_to=entry.name, evidence=evidence, date=today(), flags=tuple(flags),
        source_type=SOURCE_PEPPOL_DIRECTORY, page_title="Peppol Directory",
        published_address=entry.geo, name_match=verdict.match, address_match=level,
        match_score=match_score(verdict.match, {**parts, "domain": None, "register": None}),
        validation_status=validation, validation_date=date, issues=tuple(issues),
        address_text=entry.geo,
    )
