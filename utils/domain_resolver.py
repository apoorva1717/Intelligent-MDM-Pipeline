"""Single write path for the ``domain`` / ``website_url`` fields.

Every value those two fields ever take is produced here. Callers hand in the
candidate URL they found plus whatever evidence the record carries; this module
canonicalises the URL and decides whether the resulting domain may be attributed
to the organisation at all.

Two separate concerns:

**Canonical form** — :func:`canonicalise_domain` reduces any candidate URL to the
registrable domain (scheme, ``www.``, path, query, fragment and trailing slash
removed; subdomains collapsed) by reusing
:func:`utils.text_utils.extract_domain`. ``website_url`` is then rebuilt as
``https://<domain>`` so it can never carry a deep path
(``…/home/index.en.html``) or a sub-site host (``investors.lockheedmartin.com``).
:func:`canonicalise_host` is the department-domain counterpart: it strips the
same URL parts but **keeps** the subdomain, because a department domain
legitimately *is* a subdomain (``chemistry.stanford.edu``).

**Ownership** — ROR has a country guard and GLEIF a name-verification guard,
because both upstream scorers return confident wrong answers. The domain path
had neither, so an unrelated company's website could be attached to a customer
record and read as successful enrichment (``delta.com`` for "Delta Analytical").
:func:`resolve_domain` closes that gap: a candidate is accepted only with
registry provenance, a name-similarity match, corroborating email evidence, or
on-domain search evidence. Otherwise ``domain`` is left empty and the caller
flags the record ``domain-unverified``.

Configuration: ``DOMAIN_NAME_MATCH_THRESHOLD`` (name-similarity cut-off) and
``DOMAIN_OWNERSHIP_GUARD_ENABLED`` (kill switch so the guard can be A/B
disabled), both on ``config.Settings`` — see the LEI_LOOKUP_ENABLED pattern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from rapidfuzz import fuzz

from enrichment.provenance import (
    DETERMINISTIC,
    FUZZY_RATIO,
    GUARD_DOMAIN_OWNERSHIP,
    REGISTRY_EXACT,
    Evidence,
)
from enrichment.tier1_ror import _normalise_for_tokens
from utils.text_utils import extract_domain

# Flag code raised when a candidate domain fails every ownership condition.
DOMAIN_UNVERIFIED_CODE = "domain-unverified"
DOMAIN_UNVERIFIED_REASON = (
    "domain-unverified: website could not be verified as belonging to this "
    "organisation — verify"
)

# Free / consumer mailbox providers. An address at one of these says nothing
# about which organisation the record belongs to, so it can never corroborate a
# candidate domain. Matched on the registrable domain's LABEL so every country
# variant is covered (``yahoo.co.uk``, ``live.de``, …).
_GENERIC_EMAIL_LABELS: frozenset[str] = frozenset({
    "gmail", "googlemail", "outlook", "hotmail", "live", "yahoo", "ymail",
    "aol", "icloud", "me", "mac", "gmx", "protonmail", "proton", "mail",
    "msn", "zoho", "yandex", "fastmail", "hushmail", "inbox", "email",
})
# Providers whose label is too generic to blocklist on its own.
_GENERIC_EMAIL_DOMAINS: frozenset[str] = frozenset({
    "web.de", "t-online.de", "freenet.de", "orange.fr", "wanadoo.fr",
    "libero.it", "bt.com", "comcast.net", "verizon.net", "sbcglobal.net",
})

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
_EMAIL_SPLIT_RE = re.compile(r"[;,\s]+")

# Minimum token length that counts as "significant" when checking whether a
# page title / H1 names the organisation. Mirrors the website resolver's
# name-overlap rule (utils are shared, the constant is not exported there).
_SIGNIFICANT_TOKEN_LEN = 4


# ---------------------------------------------------------------------------
# Canonical form
# ---------------------------------------------------------------------------

def _with_scheme(url: str) -> str:
    """``extract_domain`` parses via urlparse, which only exposes a hostname
    when a scheme is present — so a bare host ('lockheedmartin.com') would
    otherwise parse as a path. Add one when it is missing."""
    url = url.strip()
    if not url:
        return url
    if _SCHEME_RE.match(url):
        return url
    return f"https://{url}"


def canonicalise_domain(url: str | None) -> str | None:
    """Registrable domain for *url* — the value the ``domain`` field carries.

    Strips scheme, ``www.``, path, query, fragment and any trailing slash, then
    collapses subdomains via :func:`utils.text_utils.extract_domain`::

        http://www.uni-stuttgart.de/home/index.en.html  → uni-stuttgart.de
        https://investors.lockheedmartin.com            → lockheedmartin.com
        https://www.example.co.uk/page?q=1#top          → example.co.uk

    Accepts a bare host as well as a full URL. Returns ``None`` when nothing
    usable is left.
    """
    if not url or not url.strip():
        return None
    domain = extract_domain(_with_scheme(url))
    if not domain:
        return None
    domain = domain.strip().lower().rstrip(".")
    return domain or None


def canonicalise_host(url: str | None) -> str | None:
    """Full host for *url*, **without** collapsing subdomains — the
    ``department_domain`` counterpart to :func:`canonicalise_domain`.

    Department domains legitimately *are* subdomains (``chemistry.stanford.edu``,
    ``be.mit.edu``), so collapsing them would destroy the Tier 2B output. Only
    the path / query / fragment / trailing slash and a leading ``www.`` are
    removed::

        https://medschool.umich.edu/departments/radiation-oncology
            → medschool.umich.edu
    """
    if not url or not url.strip():
        return None
    try:
        host = (urlparse(_with_scheme(url)).hostname or "").strip().lower()
    except Exception:
        return None
    host = host.rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def website_url_for(domain: str | None) -> str | None:
    """The homepage URL emitted alongside an accepted *domain*."""
    return f"https://{domain}" if domain else None


def domain_label(domain: str | None) -> str | None:
    """The registrable label — everything before the public suffix.

    ``uni-stuttgart.de`` → ``uni-stuttgart``; ``example.co.uk`` → ``example``.
    """
    if not domain:
        return None
    head = domain.split(".")[0]
    return head or None


# ---------------------------------------------------------------------------
# Evidence / decision types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DomainEvidence:
    """What the caller knows about the record and where the candidate came from.

    Parameters
    ----------
    name1
        The organisation name the domain is claimed to belong to.
    email
        The record's email address, if any (used for condition 3).
    registry
        ``"ROR"`` or ``"GLEIF"`` when the candidate came out of a registry
        record that already passed that registry's own guard (ROR's country
        guard / GLEIF's name verification). ``None`` for web-derived candidates.
    serp_title, serp_h1
        Title / H1 of the search result the candidate came from (condition 4).
    serp_url
        URL of that search result — the on-domain check requires it to be on the
        candidate's own domain.
    """
    name1: str | None = None
    email: str | None = None
    registry: str | None = None
    serp_title: str | None = None
    serp_h1: str | None = None
    serp_url: str | None = None


@dataclass(frozen=True)
class DomainDecision:
    """Outcome of one :func:`resolve_domain` call.

    ``domain`` / ``website_url`` are what the caller writes (both ``None`` on a
    rejection). ``verified_by`` names the ownership condition that carried it:
    ``registry`` | ``email`` | ``name`` | ``serp`` — or ``unguarded`` when
    ``DOMAIN_OWNERSHIP_GUARD_ENABLED`` is off.
    """
    domain: str | None = None
    website_url: str | None = None
    verified_by: str | None = None
    rejected: bool = False
    candidate: str | None = None

    @property
    def accepted(self) -> bool:
        return self.domain is not None


# ---------------------------------------------------------------------------
# Ownership conditions
# ---------------------------------------------------------------------------

def email_domain(email: str | None) -> str | None:
    """Registrable domain of *email*, or ``None`` when absent or generic.

    A record may carry several addresses; the first one with a usable,
    non-generic domain wins.
    """
    if not email or not email.strip():
        return None
    for part in _EMAIL_SPLIT_RE.split(email.strip()):
        if "@" not in part:
            continue
        host = part.rsplit("@", 1)[-1].strip().strip("<>()[]").rstrip(".")
        domain = canonicalise_domain(host)
        if not domain or is_generic_email_domain(domain):
            continue
        return domain
    return None


def is_generic_email_domain(domain: str | None) -> bool:
    """True for consumer mailbox providers (gmail, web.de, …)."""
    if not domain:
        return True
    if domain in _GENERIC_EMAIL_DOMAINS:
        return True
    return (domain_label(domain) or "") in _GENERIC_EMAIL_LABELS


def name_similarity(name1: str | None, domain: str | None) -> float:
    """RapidFuzz ``token_sort_ratio`` between Name 1 and the domain label.

    Name 1 is normalised with the existing
    :func:`enrichment.tier1_ror._normalise_for_tokens` (dashes and punctuation
    to spaces, legal suffixes canonicalised). The domain label is split on
    hyphens only — concatenated words are deliberately **not** segmented, since
    guessing word boundaries inside ``aumbiotech`` produces false confidence.
    """
    label = domain_label(domain)
    if not name1 or not name1.strip() or not label:
        return 0.0
    name_side = _normalise_for_tokens(name1)
    if not name_side:
        return 0.0
    return float(fuzz.token_sort_ratio(name_side, label.replace("-", " ")))


def _significant_tokens(name1: str | None) -> set[str]:
    if not name1:
        return set()
    return {
        tok for tok in _normalise_for_tokens(name1).split()
        if len(tok) >= _SIGNIFICANT_TOKEN_LEN
    }


def has_on_domain_evidence(evidence: DomainEvidence, domain: str | None) -> bool:
    """Condition 4 — the candidate came from a search result **on that domain**
    whose page title or H1 names the organisation.

    Every significant Name-1 token must appear, not just one: the SERP layer
    already admits a result on a single ≥4-char overlap, which is exactly how a
    stranger's page ("… Biotech …") slips through.
    """
    if not domain:
        return False
    if canonicalise_domain(evidence.serp_url) != domain:
        return False
    haystack = " ".join(
        part for part in (evidence.serp_title, evidence.serp_h1) if part
    ).lower()
    if not haystack.strip():
        return False
    tokens = _significant_tokens(evidence.name1)
    if not tokens:
        return False
    return all(tok in haystack for tok in tokens)


# ---------------------------------------------------------------------------
# The chokepoint
# ---------------------------------------------------------------------------

def _settings_defaults(threshold: float | None, guard_enabled: bool | None):
    if threshold is not None and guard_enabled is not None:
        return threshold, guard_enabled
    from config import get_settings  # local import — avoids an import cycle
    settings = get_settings()
    if threshold is None:
        threshold = settings.domain_name_match_threshold
    if guard_enabled is None:
        guard_enabled = settings.domain_ownership_guard_enabled
    return threshold, guard_enabled


def resolve_domain(
    candidate_url: str | None,
    evidence: DomainEvidence | None = None,
    *,
    threshold: float | None = None,
    guard_enabled: bool | None = None,
) -> DomainDecision:
    """Canonicalise *candidate_url* and decide whether it may be attributed to
    this organisation. The only place ``domain`` / ``website_url`` are decided.

    Precedence, first hit wins:

    1. **Registry provenance** — the candidate came from a ROR record that
       passed the country guard, or a GLEIF record that passed name
       verification. Sufficient on its own.
    2. **Name similarity** — ``token_sort_ratio`` at or above the threshold.
       Checked before the email so a well-matched candidate is never clobbered
       by an unrelated address on the record (a distributor's mailbox).
    3. **Email evidence** — the record carries a non-generic email domain. When
       the candidate could not be verified on its own, this *replaces* it: a
       record holding ``ORDERS@MERIDIANLABS.COM`` already knows the
       organisation's domain better than a search result does
       (``meridianlabs.ai``).
    4. **On-domain search evidence** — the candidate's own page names the org.

    None of the above → ``domain`` and ``website_url`` stay empty and
    ``rejected`` is set, so the caller can raise ``domain-unverified``.
    Attaching an unrelated company's website is worse than an empty field,
    because it reads as successful enrichment.
    """
    evidence = evidence or DomainEvidence()
    candidate = canonicalise_domain(candidate_url)
    if not candidate:
        return DomainDecision()

    threshold, guard_enabled = _settings_defaults(threshold, guard_enabled)

    def accept(domain: str, verified_by: str) -> DomainDecision:
        return DomainDecision(
            domain=domain,
            website_url=website_url_for(domain),
            verified_by=verified_by,
            candidate=candidate,
        )

    if evidence.registry:
        return accept(candidate, "registry")

    if not guard_enabled:
        # A/B kill switch: canonicalisation still applies, ownership does not.
        return accept(candidate, "unguarded")

    if name_similarity(evidence.name1, candidate) >= threshold:
        return accept(candidate, "name")

    from_email = email_domain(evidence.email)
    if from_email:
        return accept(from_email, "email")

    if has_on_domain_evidence(evidence, candidate):
        return accept(candidate, "serp")

    return DomainDecision(rejected=True, candidate=candidate)


# ---------------------------------------------------------------------------
# The write (Fix 10)
# ---------------------------------------------------------------------------

#: How each ownership condition is attributed. The condition that accepted the
#: candidate IS its provenance: "registry" means ROR/GLEIF vouched for it,
#: "name" means a scored string comparison did, and those are not the same
#: claim — which is exactly why the scale travels with the value.
_VERIFIED_BY_SCALE: dict[str, str] = {
    "registry": REGISTRY_EXACT,
    "name": FUZZY_RATIO,
    "email": DETERMINISTIC,
    "serp": DETERMINISTIC,
    "unguarded": DETERMINISTIC,
}


def write_domain(
    record,
    candidate_url: str | None,
    evidence: DomainEvidence,
    *,
    producer_chain: tuple[str, ...] = ("website_resolver",),
    registry_identifier: str | None = None,
    threshold: float | None = None,
    guard_enabled: bool | None = None,
    tier: int | None = None,
) -> DomainDecision:
    """Decide a candidate domain and write it through ``record.write``.

    This is Fix 1's chokepoint, now attributing what it writes rather than
    running as a parallel mechanism: :func:`resolve_domain` still takes the
    decision, and the decision's ``verified_by`` — the ownership condition that
    carried it — becomes the provenance of the value. A candidate the guard
    refuses is recorded as a guard rejection (Step 4) instead of vanishing: the
    pipeline had a confident answer and deliberately declined it, which is the
    case most worth being able to defend afterwards.

    ``record`` is an :class:`enrichment.provenance.EnrichedRecord`. Everything
    other than ``domain`` (``website_url``, the transient raw candidate, the
    verified-by telemetry) is unscoped and written directly, exactly as before.
    """
    decision = resolve_domain(
        candidate_url, evidence,
        threshold=threshold, guard_enabled=guard_enabled,
    )

    if decision.domain:
        scale = _VERIFIED_BY_SCALE.get(decision.verified_by or "", DETERMINISTIC)
        if scale == FUZZY_RATIO:
            value = name_similarity(evidence.name1, decision.domain)
        elif scale == REGISTRY_EXACT:
            value = 1.0
        else:
            value = 1.0
        ref: dict[str, object] = {
            "source_url": candidate_url,
            "verified_by": decision.verified_by,
        }
        if registry_identifier:
            ref["registry_id"] = registry_identifier
        if decision.verified_by == "email":
            # The domain came off the record's own address, not the candidate.
            ref["source_url"] = None
            ref["email_domain"] = decision.domain
        record.write(
            "domain", decision.domain,
            Evidence(
                producer_chain=(
                    (evidence.registry.lower(),)
                    if decision.verified_by == "registry" and evidence.registry
                    else ("record_email",)
                    if decision.verified_by == "email"
                    else producer_chain
                ),
                tier=tier,
                confidence_scale=scale,
                confidence_value=value,
                evidence_ref=ref,
                rule_id=f"domain-ownership:{decision.verified_by}",
            ),
        )
        record["website_url"] = decision.website_url
        record["_website_raw"] = candidate_url
        record["domain_verified_by"] = decision.verified_by
        # A later, verified candidate clears an earlier rejection.
        record["domain_rejected"] = False
        record.pop("_domain_unverified", None)
    elif decision.rejected:
        record["domain_rejected"] = True
        record["_domain_unverified"] = True
        record.reject(
            "domain", decision.candidate, GUARD_DOMAIN_OWNERSHIP,
            reason=(
                "no ownership condition held: not from a registry, name "
                "similarity below threshold, no non-generic email domain, "
                "and no on-domain search evidence"
            ),
            evidence=Evidence(
                producer_chain=producer_chain,
                tier=tier,
                confidence_scale=FUZZY_RATIO,
                confidence_value=name_similarity(
                    evidence.name1, decision.candidate,
                ),
                evidence_ref={
                    "source_url": candidate_url,
                    "claimed_for": evidence.name1,
                },
                rule_id="domain-ownership-guard",
            ),
        )
    return decision
