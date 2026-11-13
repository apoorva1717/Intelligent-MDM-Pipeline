"""Which email domain is the customer's own website (2026-11-12).

For a Not found row with no website found, the customer's email domain
("info@firma-xyz.de") is a website we never searched for. A domain is
used (:func:`own_domain`) only when it is not:

* a mailbox provider (gmail.com, t-online.de, outlook.com, …): anyone's
  address;
* our own company's (bruker.com, …): an address of ours entered on the
  customer (87 of the 334 NO_WEBSITE_FOUND rows with an email);
* an administration's umbrella domain (bund.de, bundeswehr.org, sachsen.de,
  …): one domain for many authorities;
* shared by several customers (:func:`shared_domains`: at least
  :data:`SHARED_MIN` different legal entities in the workbook use it): a
  portal or service provider (frauenaerzte-im-netz.de, zontal.io), or a
  group domain whose legal notice lists many companies.

"E-Mail Address" is tried before "Clrk's internet add."; the first one that
passes is used.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from vat.entity_match import _MAIL_PROVIDERS, registrable_domain

#: Mailbox providers beyond vat.entity_match's list.
MAIL_PROVIDERS = _MAIL_PROVIDERS | frozenset({
    "online.de", "arcor.de", "gmx.at", "gmx.ch", "gmx.com", "web.com", "outlook.de", "hotmail.de", "live.de",
    "live.com", "msn.com", "me.com", "mac.com", "yahoo.co.uk", "aol.de", "mailbox.org", "protonmail.com",
    "proton.me", "email.de", "1und1.de", "vodafone.de", "kabelbw.de", "ewe.net", "htp-tel.de", "unitybox.de",
    "versanet.de", "nexgo.de", "freenet.net", "googlemail.de", "icloud.de", "mail.ru", "yandex.ru", "qq.com",
    "163.com", "126.com",
})
#: Our own company: an address of ours on a customer is never the customer's.
OWN_PREFIXES = ("bruker",)
#: Administrations' umbrella domains (federal, armed forces, the Länder).
UMBRELLA = frozenset({
    "bund.de", "bundeswehr.org", "bundeswehr.de", "bwl.de", "sachsen.de", "bayern.de", "berlin.de",
    "hamburg.de", "sachsen-anhalt.de", "mv-regierung.de", "nrw.de", "niedersachsen.de", "hessen.de",
    "thueringen.de", "brandenburg.de", "bremen.de", "saarland.de", "rlp.de", "schleswig-holstein.de",
    "landsh.de", "baden-wuerttemberg.de", "polizei.de", "dataport.de",
})
SHARED_MIN = 3


def _usable(domain: str | None) -> bool:
    return bool(domain) and domain not in MAIL_PROVIDERS and domain not in UMBRELLA \
        and not domain.startswith(OWN_PREFIXES)


def domain_of(email: str | None) -> str | None:
    return registrable_domain(email) if email and "@" in str(email) else None


def shared_domains(records: Iterable[tuple[str | None, Iterable[str | None]]]) -> frozenset[str]:
    """Domains used by at least SHARED_MIN different entities; *records* are
    ``(entity key, emails)`` for every row of the workbook."""
    users: dict[str, set[str]] = defaultdict(set)
    for key, emails in records:
        for e in emails:
            d = domain_of(e)
            if d and key:
                users[d].add(key)
    return frozenset(d for d, keys in users.items() if len(keys) >= SHARED_MIN)


def own_domain(emails: Iterable[str | None], shared: frozenset[str] = frozenset()) -> str | None:
    """The first email's domain that is the customer's own (see the module
    docstring); None when there is none."""
    for e in emails:
        d = domain_of(e)
        if _usable(d) and d not in shared:
            return d
    return None


def why_not(emails: Iterable[str | None], shared: frozenset[str] = frozenset()) -> str:
    """Why no domain is used (for the count): NO_EMAIL / MAIL_PROVIDER /
    OWN_COMPANY / UMBRELLA / SHARED (of the first address); USED when one is used."""
    emails = list(emails)
    domains = [d for d in (domain_of(e) for e in emails) if d]
    if not domains:
        return "NO_EMAIL"
    if own_domain(emails, shared):
        return "USED"
    d = domains[0]
    if d in MAIL_PROVIDERS:
        return "MAIL_PROVIDER"
    if d.startswith(OWN_PREFIXES):
        return "OWN_COMPANY"
    if d in UMBRELLA:
        return "UMBRELLA"
    return "SHARED" if d in shared else "USED"
