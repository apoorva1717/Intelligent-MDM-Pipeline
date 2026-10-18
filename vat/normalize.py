"""VAT ID normalisation and offline format check.

Two steps, both deterministic and offline:

1. :func:`normalize_vat` — the SAP value (KNA1-STCEG, "VAT Registration No.")
   to the compact form VIES takes: separators removed, upper-cased, the
   country prefix taken from ``Country/Region Key`` when the value has none.
   Greece's VAT prefix is ``EL`` (VIES lists ``EL``, never ``GR``), so a
   ``GR`` prefix and the ISO key ``GR`` both map to ``EL``. Northern Ireland
   is ``XI``.
2. :func:`check_format` — python-stdnum's per-country syntax and checksum.

**Flagging over inventing.** Nothing here guesses. A value whose prefix
disagrees with the Country/Region Key keeps its own prefix and is flagged
``PREFIX_COUNTRY_MISMATCH``; it is not rewritten to the key's country. An
Austrian value missing its ``U`` is not given one. A ``GB`` key never turns a
bare number into ``XI``: only a value that already says ``XI`` is checked as
Northern Ireland. The raw value is always kept alongside the normalised one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from stdnum.eu import vat as eu_vat
from stdnum.exceptions import ValidationError

# ── Format states ────────────────────────────────────────────────────────────
MISSING = "MISSING"            # no value, and Country/Region Key is a VIES state (or blank)
NOT_EU = "NOT_EU"              # no EU VAT prefix, and the Country/Region Key is not a VIES state
FORMAT_ERROR = "FORMAT_ERROR"  # EU prefix, but stdnum rejects syntax or checksum
FORMAT_OK = "FORMAT_OK"        # EU prefix and stdnum accepts it — eligible for VIES

# ── Flags (VAT_Flags column values, not issue codes) ─────────────────────────
#: The value's prefix names a different country than Country/Region Key.
PREFIX_COUNTRY_MISMATCH = "PREFIX_COUNTRY_MISMATCH"
#: The value had no prefix; the one used came from Country/Region Key.
PREFIX_FROM_COUNTRY_KEY = "PREFIX_FROM_COUNTRY_KEY"
#: The value had no prefix and there is no Country/Region Key to take one from.
NO_PREFIX_NO_COUNTRY = "NO_PREFIX_NO_COUNTRY"
#: The value starts with letters that are not an EU VAT prefix (CHE…, GB…)
#: while Country/Region Key is an EU state. Kept as is; FORMAT_ERROR.
NON_EU_PREFIX = "NON_EU_PREFIX"

#: VAT prefixes VIES answers for (``/check-status``, verified 2026-10-15).
VIES_PREFIXES: frozenset[str] = frozenset({
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR",
    "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO",
    "SE", "SI", "SK", "XI",
})

#: Value prefixes that are read as an alias of a VIES prefix.
_PREFIX_ALIASES: dict[str, str] = {"GR": "EL"}

#: SAP Country/Region Key (ISO 3166-1 alpha-2) → the VAT prefix to add when a
#: value has none. ``GB`` is deliberately absent: a GB key does not say whether
#: a bare number is a Northern Ireland (XI) registration.
_COUNTRY_KEY_TO_PREFIX: dict[str, str] = {
    **{p: p for p in VIES_PREFIXES if p != "EL"},
    "GR": "EL",
    "EL": "EL",
}

#: Country/Region Keys consistent with each prefix, for the mismatch flag.
_PREFIX_COUNTRY_KEYS: dict[str, frozenset[str]] = {
    "EL": frozenset({"GR", "EL"}),
    "XI": frozenset({"GB", "XI"}),
}

#: Separators removed before anything else: whitespace, dots, dashes.
_SEPARATORS = re.compile(r"[\s.\-]+")


@dataclass(frozen=True)
class VatFormat:
    """One VAT value after normalisation and, where reached, the format check."""

    raw: str | None
    country_key: str | None
    #: Compact value; prefixed for an EU value. None only when MISSING.
    normalized: str | None
    #: The VIES prefix used (``EL`` for Greece), or None outside the EU.
    prefix: str | None
    #: A state above; None between :func:`normalize_vat` and :func:`check_format`.
    status: str | None
    flags: tuple[str, ...] = field(default_factory=tuple)
    #: stdnum's reason on FORMAT_ERROR (``InvalidChecksum``, ``InvalidLength`` …).
    format_error: str | None = None

    @property
    def vies_eligible(self) -> bool:
        return self.status == FORMAT_OK


def _clean_country_key(country_key: str | None) -> str | None:
    key = (country_key or "").strip().upper()
    return key or None


def normalize_vat(raw: str | None, country_key: str | None) -> VatFormat:
    """Normalise *raw*. Decides MISSING, NOT_EU and the NON_EU_PREFIX
    FORMAT_ERROR; leaves the rest to :func:`check_format` (``status`` is None
    for an EU value).

    A prefix is added from Country/Region Key only when the value has no
    leading letters at all.
    """
    key = _clean_country_key(country_key)
    raw_str = None if raw is None else str(raw)
    compact = _SEPARATORS.sub("", raw_str or "").upper()
    if not compact:
        # A blank VAT is a gap only where one is expected: a customer whose
        # Country/Region Key is outside VIES has none to miss.
        if key is not None and key not in _COUNTRY_KEY_TO_PREFIX:
            return VatFormat(raw_str, key, None, None, NOT_EU)
        return VatFormat(raw_str, key, None, None, MISSING)

    flags: list[str] = []
    lead = compact[:2]
    if lead.isalpha() and (lead in VIES_PREFIXES or lead in _PREFIX_ALIASES):
        prefix = _PREFIX_ALIASES.get(lead, lead)
        body = compact[2:]
        allowed = _PREFIX_COUNTRY_KEYS.get(prefix, frozenset({prefix}))
        if key is not None and key not in allowed:
            flags.append(PREFIX_COUNTRY_MISMATCH)
    elif compact[0].isalpha():
        # Leading letters that are not an EU prefix (CHE…, GB…, NO…). Never
        # prepend a prefix in front of them: the value is kept as it is.
        if key in _COUNTRY_KEY_TO_PREFIX:
            return VatFormat(
                raw_str, key, compact, None, FORMAT_ERROR, (NON_EU_PREFIX,),
            )
        return VatFormat(raw_str, key, compact, None, NOT_EU)
    elif key in _COUNTRY_KEY_TO_PREFIX:
        prefix = _COUNTRY_KEY_TO_PREFIX[key]
        body = compact
        flags.append(PREFIX_FROM_COUNTRY_KEY)
    else:
        if key is None:
            flags.append(NO_PREFIX_NO_COUNTRY)
        return VatFormat(raw_str, key, compact, None, NOT_EU, tuple(flags))

    return VatFormat(raw_str, key, prefix + body, prefix, None, tuple(flags))


def check_format(vat: str) -> tuple[str, str | None]:
    """stdnum syntax + checksum for a normalised, prefixed EU value.

    Returns ``(FORMAT_OK, None)`` or ``(FORMAT_ERROR, <stdnum exception name>)``.
    """
    try:
        eu_vat.validate(vat)
    except ValidationError as exc:
        return FORMAT_ERROR, type(exc).__name__
    return FORMAT_OK, None


def assess_format(raw: str | None, country_key: str | None) -> VatFormat:
    """:func:`normalize_vat` then, for an EU value, :func:`check_format`."""
    result = normalize_vat(raw, country_key)
    if result.status is not None:
        return result
    status, reason = check_format(result.normalized or "")
    return VatFormat(
        result.raw, result.country_key, result.normalized, result.prefix,
        status, result.flags, reason,
    )
